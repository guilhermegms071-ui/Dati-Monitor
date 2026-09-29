"""Portal authentication: login, lockout, limited tokens, refresh rotation, CSRF, TOTP, reset by e-mail."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pyotp
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.security import create_access_token
from app.models import AuditLog, RefreshToken, Setting, User
from tests.conftest import Factory, auth, login

pytestmark = pytest.mark.usefixtures("clean_db")


def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("dm_csrf") or ""}


async def test_login_success_sets_cookies_and_me(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    resp = await client.post(
        "/api/v1/auth/login", json={"email": t.admin_email.upper(), "password": t.admin_password}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["limited"] is None
    assert body["user"]["role"] == "reseller_admin"
    assert {"users.create", "users.update", "users.delete"} <= set(body["user"]["permissions"])
    set_cookie = resp.headers.get_list("set-cookie")
    refresh_cookie = next(c for c in set_cookie if c.startswith("dm_refresh="))
    assert "HttpOnly" in refresh_cookie
    assert "Path=/api/v1/auth" in refresh_cookie
    assert "SameSite=strict" in refresh_cookie
    me = await client.get("/api/v1/auth/me", headers=auth(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == t.admin_email
    assert me.json()["reseller_name"] == "Revenda A"


async def test_login_failures_lock_account_and_admin_unlocks(
    client: httpx.AsyncClient, factory: Factory, test_settings: Settings
) -> None:
    t = await factory.tenant()
    _, email = await factory.user(t.reseller_id, role="technician")
    for _ in range(test_settings.login_max_failures):
        resp = await client.post("/api/v1/auth/login", json={"email": email, "password": "errada-errada"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "invalid_credentials"
    locked = await client.post("/api/v1/auth/login", json={"email": email, "password": Factory.PASSWORD})
    assert locked.status_code == 423
    assert locked.json()["detail"]["code"] == "account_locked"

    admin = await login(client, t.admin_email)
    users = (await client.get("/api/v1/users", params={"q": email}, headers=auth(admin))).json()["items"]
    assert users[0]["locked_until"] is not None
    resp = await client.patch(f"/api/v1/users/{users[0]['id']}", json={"unlock": True}, headers=auth(admin))
    assert resp.status_code == 200
    assert resp.json()["locked_until"] is None
    await login(client, email)


async def test_unknown_email_and_inactive_user_get_same_error(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant()
    _, email = await factory.user(t.reseller_id, active=False)
    for e in (email, "ninguem@example.test"):
        resp = await client.post("/api/v1/auth/login", json={"email": e, "password": Factory.PASSWORD})
        assert resp.status_code == 401
        assert resp.json()["detail"]["message"] == "E-mail ou senha incorretos"


async def test_must_change_password_gives_limited_token(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    _, email = await factory.user(t.reseller_id, must_change_password=True)
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": Factory.PASSWORD})
    body = resp.json()
    assert body["limited"] == "password_change_required"
    assert "dm_refresh" not in resp.cookies
    limited = body["access_token"]
    assert (await client.get("/api/v1/auth/me", headers=auth(limited))).status_code == 200
    blocked = await client.get("/api/v1/customers", headers=auth(limited))
    assert blocked.status_code == 403

    weak = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": Factory.PASSWORD, "new_password": "curta"},
        headers=auth(limited),
    )
    assert weak.status_code == 422
    same = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": Factory.PASSWORD, "new_password": Factory.PASSWORD},
        headers=auth(limited),
    )
    assert same.json()["detail"]["code"] == "same_password"
    ok = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": Factory.PASSWORD, "new_password": "Outra-Senha-456"},
        headers=auth(limited),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["limited"] is None
    assert (await client.get("/api/v1/customers", headers=auth(ok.json()["access_token"]))).status_code == 200
    # O token limitado anterior deixa de valer quando a senha muda.
    assert (await client.get("/api/v1/auth/me", headers=auth(limited))).status_code == 401


async def test_refresh_rotation_csrf_and_reuse_detection(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    await login(client, t.admin_email)
    first_refresh = client.cookies.get("dm_refresh")
    assert first_refresh

    no_csrf = await client.post("/api/v1/auth/refresh")
    assert no_csrf.status_code == 403

    rotated = await client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert rotated.status_code == 200, rotated.text
    second_refresh = client.cookies.get("dm_refresh")
    assert second_refresh
    assert second_refresh != first_refresh

    # Reuso imediato do token antigo (duas abas): 409 sem derrubar a sessão.
    client.cookies.set("dm_refresh", first_refresh, path="/api/v1/auth")
    race = await client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert race.status_code == 409

    # Reuso depois da janela de tolerância: suspeita de roubo -> família inteira revogada.
    async with sessionmaker() as s:
        await s.execute(
            update(RefreshToken)
            .where(RefreshToken.revoked_at.is_not(None))
            .values(revoked_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        await s.commit()
    stolen = await client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert stolen.status_code == 401
    assert stolen.json()["detail"]["code"] == "refresh_reused"
    client.cookies.set("dm_refresh", second_refresh, path="/api/v1/auth")
    after = await client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert after.status_code == 401


async def test_logout_revokes_refresh(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    await login(client, t.admin_email)
    raw = client.cookies.get("dm_refresh")
    assert raw
    out = await client.post("/api/v1/auth/logout", headers=_csrf(client))
    assert out.status_code == 200
    client.cookies.set("dm_refresh", raw, path="/api/v1/auth")
    client.cookies.set("dm_csrf", "x", path="/")
    resp = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "x"})
    assert resp.status_code == 401


async def test_expired_refresh_and_missing_cookie(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    await login(client, t.admin_email)
    async with sessionmaker() as s:
        await s.execute(update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
    resp = await client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert resp.json()["detail"]["code"] == "refresh_expired"
    client.cookies.delete("dm_refresh", path="/api/v1/auth")
    resp = await client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert resp.json()["detail"]["code"] in ("refresh_missing", "refresh_invalid")


async def test_invalid_and_expired_access_tokens(
    client: httpx.AsyncClient, factory: Factory, test_settings: Settings
) -> None:
    t = await factory.tenant()
    assert (await client.get("/api/v1/customers")).status_code == 401
    bad = await client.get("/api/v1/customers", headers=auth("abc.def.ghi"))
    assert bad.json()["detail"]["code"] == "token_invalid"
    token, _ = create_access_token(
        secret=test_settings.jwt_secret.get_secret_value(),
        user_id=uuid.uuid4(),
        reseller_id=t.reseller_id,
        role="superadmin",
        customer_id=None,
        minutes=1,
        now=datetime.now(UTC) - timedelta(minutes=5),
    )
    expired = await client.get("/api/v1/customers", headers=auth(token))
    assert expired.status_code == 401
    forged, _ = create_access_token(
        secret="outro-segredo-" + "y" * 40,
        user_id=uuid.uuid4(),
        reseller_id=t.reseller_id,
        role="superadmin",
        customer_id=None,
        minutes=5,
    )
    assert (await client.get("/api/v1/customers", headers=auth(forged))).status_code == 401


async def test_deactivated_user_token_stops_working(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    uid, email = await factory.user(t.reseller_id, role="technician")
    token = await login(client, email)
    admin = await login(client, t.admin_email)
    assert (
        await client.patch(f"/api/v1/users/{uid}", json={"active": False}, headers=auth(admin))
    ).status_code == 200
    resp = await client.get("/api/v1/customers", headers=auth(token))
    assert resp.status_code == 401


async def _enable_totp(client: httpx.AsyncClient, token: str) -> str:
    setup = await client.post("/api/v1/auth/totp/setup", headers=auth(token))
    assert setup.status_code == 200, setup.text
    secret: str = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    wrong = await client.post("/api/v1/auth/totp/enable", json={"code": "000000"}, headers=auth(token))
    assert wrong.status_code == 400
    ok = await client.post(
        "/api/v1/auth/totp/enable", json={"code": pyotp.TOTP(secret).now()}, headers=auth(token)
    )
    assert ok.status_code == 200, ok.text
    return secret


async def test_totp_flow_with_replay_protection(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    token = await login(client, t.admin_email)
    secret = await _enable_totp(client, token)
    again = await client.post("/api/v1/auth/totp/setup", headers=auth(token))
    assert again.json()["detail"]["code"] == "totp_already_enabled"

    need = await client.post(
        "/api/v1/auth/login", json={"email": t.admin_email, "password": Factory.PASSWORD}
    )
    assert need.status_code == 401
    assert need.json()["detail"]["code"] == "totp_required"
    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": t.admin_email, "password": Factory.PASSWORD, "totp_code": "123456"},
    )
    assert bad.json()["detail"]["code"] == "totp_invalid"

    # Próximo passo ainda não usado: aceito uma vez; o mesmo código de novo é recusado (replay).
    async with sessionmaker() as s:
        user = (await s.execute(select(User).where(User.email == t.admin_email))).scalar_one()
        last_step = user.totp_last_step
    assert last_step is not None
    totp = pyotp.TOTP(secret)
    code = totp.generate_otp(last_step + 1)
    ok = await client.post(
        "/api/v1/auth/login", json={"email": t.admin_email, "password": Factory.PASSWORD, "totp_code": code}
    )
    assert ok.status_code == 200, ok.text
    replay = await client.post(
        "/api/v1/auth/login", json={"email": t.admin_email, "password": Factory.PASSWORD, "totp_code": code}
    )
    assert replay.json()["detail"]["code"] == "totp_invalid"

    fresh = ok.json()["access_token"]
    disable_bad = await client.post(
        "/api/v1/auth/totp/disable", json={"password": "errada-errada", "code": "000000"}, headers=auth(fresh)
    )
    assert disable_bad.status_code == 400
    # Simula a passagem do tempo (a proteção contra reuso só aceita passos ainda não usados).
    async with sessionmaker() as s:
        await s.execute(update(User).where(User.email == t.admin_email).values(totp_last_step=last_step - 10))
        await s.commit()
    disabled = await client.post(
        "/api/v1/auth/totp/disable",
        json={"password": Factory.PASSWORD, "code": totp.now()},
        headers=auth(fresh),
    )
    assert disabled.status_code == 200, disabled.text
    await login(client, t.admin_email)


async def test_reseller_can_require_totp_for_admins(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    async with sessionmaker() as s:
        s.add(
            Setting(
                reseller_id=t.reseller_id, key="security.require_totp_for_admins", value={"enabled": True}
            )
        )
        await s.commit()
    resp = await client.post(
        "/api/v1/auth/login", json={"email": t.admin_email, "password": Factory.PASSWORD}
    )
    assert resp.json()["limited"] == "totp_setup_required"
    limited = resp.json()["access_token"]
    assert (await client.get("/api/v1/users", headers=auth(limited))).status_code == 403
    secret = await _enable_totp(client, limited)
    code = pyotp.TOTP(secret).generate_otp(int(datetime.now(UTC).timestamp()) // 30 + 1)
    full = await login(client, t.admin_email, totp_code=code)
    assert (await client.get("/api/v1/users", headers=auth(full))).status_code == 200


async def test_forgot_and_reset_password_by_email(
    client: httpx.AsyncClient, factory: Factory, mail_catcher: Any
) -> None:
    t = await factory.tenant()
    old_token = await login(client, t.admin_email)
    unknown = await client.post("/api/v1/auth/forgot-password", json={"email": "ninguem@example.test"})
    assert unknown.status_code == 202
    assert mail_catcher.store.list() == []

    resp = await client.post("/api/v1/auth/forgot-password", json={"email": t.admin_email})
    assert resp.status_code == 202
    mails = mail_catcher.store.list()
    assert len(mails) == 1
    assert mails[0]["to"] == [t.admin_email]
    match = re.search(r"http://portal\.test/redefinir-senha\?token=(\S+)", mails[0]["text"])
    assert match, mails[0]["text"]
    reset_token = match.group(1)

    ok = await client.post(
        "/api/v1/auth/reset-password", json={"token": reset_token, "new_password": "Nova-Senha-789"}
    )
    assert ok.status_code == 200, ok.text
    reused = await client.post(
        "/api/v1/auth/reset-password", json={"token": reset_token, "new_password": "Mais-Uma-Senha-1"}
    )
    assert reused.json()["detail"]["code"] == "reset_token_invalid"
    assert (await client.get("/api/v1/customers", headers=auth(old_token))).status_code == 401
    await login(client, t.admin_email, "Nova-Senha-789")


async def test_login_rate_limit(api_app: Any, client: httpx.AsyncClient, factory: Factory) -> None:
    from app.core.ratelimit import RateLimiter  # noqa: PLC0415

    api_app.state.login_limiter = RateLimiter(3, 60)
    for _ in range(3):
        await client.post("/api/v1/auth/login", json={"email": "x@y.test", "password": "zzzzzzzzzz"})
    resp = await client.post("/api/v1/auth/login", json={"email": "x@y.test", "password": "zzzzzzzzzz"})
    assert resp.status_code == 429
    assert resp.json()["detail"]["retry_after_seconds"] >= 0


async def test_login_is_audited_and_preferences_are_saved(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    await client.post("/api/v1/auth/login", json={"email": t.admin_email, "password": "errada-errada"})
    token = await login(client, t.admin_email)
    prefs = await client.patch(
        "/api/v1/auth/me/preferences",
        json={"preferences": {"park_columns": ["status", "serial"]}},
        headers=auth(token),
    )
    assert prefs.json()["preferences"]["park_columns"] == ["status", "serial"]
    async with sessionmaker() as s:
        actions = [
            a.action for a in (await s.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars()
        ]
    assert "auth.login_failed" in actions
    assert "auth.login" in actions
