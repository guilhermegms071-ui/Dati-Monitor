"""Notifications (PROMPT 9): e-mail through a real SMTP server, quiet hours, suppression, webhook with
signature and retry, WhatsApp (Meta and generic), channel API with masked secrets, daily summary."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.alerts import http_client
from app.core.config import Settings
from app.core.principal import Principal
from app.models import Alert, AlertRule, Notification
from app.schemas.alerts import ChannelIn, NotificationSettings
from app.services import notifications as svc
from app.services.alerts import open_alert
from app.services.notifiers import (
    GenericWhatsAppNotifier,
    Message,
    MetaWhatsAppNotifier,
    NotifyError,
    normalize_phone,
)
from tests.alert_helpers import mock_transport
from tests.conftest import Factory, Tenant, auth, login

SP = ZoneInfo("America/Sao_Paulo")


async def principal(maker: async_sessionmaker[AsyncSession], t: Tenant) -> Principal:
    from app.core.permissions import ROLE_PERMISSIONS  # noqa: PLC0415
    from app.models import User  # noqa: PLC0415

    async with maker() as s:
        user_id = (await s.execute(select(User.id).where(User.email == t.admin_email))).scalar_one()
    return Principal(
        user_id=user_id,
        reseller_id=t.reseller_id,
        role="reseller_admin",
        customer_id=None,
        permissions=ROLE_PERMISSIONS["reseller_admin"],
        email=t.admin_email,
    )


async def _channel(
    maker: async_sessionmaker[AsyncSession], settings: Settings, t: Tenant, data: ChannelIn
) -> uuid.UUID:
    async with maker() as s:
        out = await svc.create_channel(s, settings, await principal(maker, t), data)
        await s.commit()
    return out.id


async def _alert(
    maker: async_sessionmaker[AsyncSession], t: Tenant, type_: str, severity: str, key: str
) -> uuid.UUID:
    async with maker() as s:
        alert_id = await open_alert(
            s,
            reseller_id=t.reseller_id,
            type_=type_,
            severity=severity,
            target_type="device",
            target_id=uuid.uuid4(),
            message=f"Mensagem {key}",
            dedup_key=key,
            customer_id=t.customer_id,
        )
        await s.commit()
    assert alert_id is not None
    return alert_id


async def _enqueue_and_deliver(
    maker: async_sessionmaker[AsyncSession], settings: Settings, client: httpx.AsyncClient, now: datetime
) -> svc.DeliveryResult:
    async with maker() as s:
        await svc.enqueue_new_alerts(s, settings, now)
        result = await svc.deliver_due(s, settings, client, now)
        await s.commit()
    return result


async def _rows(maker: async_sessionmaker[AsyncSession]) -> list[Notification]:
    async with maker() as s:
        return list((await s.execute(select(Notification).order_by(Notification.created_at))).scalars())


async def test_email_quiet_hours_and_suppression(
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    mail_catcher: Any,
) -> None:
    t = await factory.tenant()
    await _channel(
        sessionmaker,
        test_settings,
        t,
        ChannelIn(kind="email", name="Suporte", recipients=["suporte@cliente.test", "noc@cliente.test"]),
    )
    async with sessionmaker() as s:
        from app.services.alert_rules import ensure_default_rules  # noqa: PLC0415

        await ensure_default_rules(s, t.reseller_id)
        await s.commit()
    critical = await _alert(sessionmaker, t, "agent_offline", "critical", "k-crit")
    warning = await _alert(sessionmaker, t, "toner_low", "warning", "k-warn")

    night = datetime(2026, 9, 29, 23, 30, tzinfo=SP).astimezone(UTC)
    async with httpx.AsyncClient() as client:
        result = await _enqueue_and_deliver(sessionmaker, test_settings, client, night)
    # Crítico sai na hora (2 destinatários); o aviso espera o fim do silêncio (07:00).
    assert result.sent == 2
    rows = await _rows(sessionmaker)
    assert {(r.alert_id, r.status) for r in rows} == {(critical, "sent"), (warning, "pending")}
    pending = [r for r in rows if r.status == "pending"]
    assert all(r.next_attempt_at == datetime(2026, 9, 30, 7, 0, tzinfo=SP).astimezone(UTC) for r in pending)
    mails = mail_catcher.store.list()
    assert sorted(m["to"][0] for m in mails) == ["noc@cliente.test", "suporte@cliente.test"]
    assert mails[0]["subject"].startswith("[Dati Monitor] CRÍTICO — Coletor sem sinal: Mensagem k-crit")
    assert f"http://portal.test/alertas?alerta={critical}" in mails[0]["text"]

    # Resolvido durante a madrugada: às 07:00 não envia (suprimido, com o motivo).
    async with sessionmaker() as s:
        await s.execute(update(Alert).where(Alert.id == warning).values(state="resolved"))
        await s.commit()
    async with httpx.AsyncClient() as client, sessionmaker() as s:
        morning = datetime(2026, 9, 30, 7, 1, tzinfo=SP).astimezone(UTC)
        result = await svc.deliver_due(s, test_settings, client, morning)
        await s.commit()
    assert (result.sent, result.suppressed) == (0, 2)
    assert {r.error for r in await _rows(sessionmaker) if r.status == "suppressed"} == {
        "alerta resolvido antes do envio"
    }

    # Regra desligada: o alerta aparece no portal, mas não notifica.
    async with sessionmaker() as s:
        await s.execute(update(AlertRule).where(AlertRule.type == "paper_jam").values(enabled=False))
        await s.commit()
    await _alert(sessionmaker, t, "paper_jam", "warning", "k-off")
    async with httpx.AsyncClient() as client:
        await _enqueue_and_deliver(sessionmaker, test_settings, client, morning)
    assert len(await _rows(sessionmaker)) == 4
    async with sessionmaker() as s:
        assert (
            await s.execute(select(Alert.notified_at).where(Alert.dedup_key == "k-off"))
        ).scalar_one() is not None


async def test_webhook_signature_retry_and_final_failure(
    factory: Factory, sessionmaker: async_sessionmaker[AsyncSession], test_settings: Settings
) -> None:
    t = await factory.tenant()
    await _channel(
        sessionmaker,
        test_settings,
        t,
        ChannelIn(kind="webhook", name="ERP", config={"url": "https://hooks.test/dati", "secret": "s3gr3d0"}),
    )
    await _alert(sessionmaker, t, "agent_offline", "critical", "k-hook")
    seen: list[httpx.Request] = []
    now = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
    async with httpx.AsyncClient(transport=mock_transport([500, 503, 200], seen)) as client:
        r1 = await _enqueue_and_deliver(sessionmaker, test_settings, client, now)
        assert (r1.sent, r1.retried) == (0, 1)
        row = (await _rows(sessionmaker))[0]
        assert row.next_attempt_at == now + timedelta(minutes=1)
        assert "HTTP 500" in (row.error or "")
        async with sessionmaker() as s:
            assert (
                await svc.deliver_due(s, test_settings, client, now + timedelta(seconds=30))
            ).sent == 0  # espera
            r2 = await svc.deliver_due(s, test_settings, client, now + timedelta(minutes=1))
            r3 = await svc.deliver_due(s, test_settings, client, now + timedelta(minutes=4))
            await s.commit()
    assert (r2.retried, r3.sent) == (1, 1)
    row = (await _rows(sessionmaker))[0]
    assert (row.status, row.attempts, row.error) == ("sent", 3, None)
    body = seen[-1].content
    expected = "sha256=" + hmac.new(b"s3gr3d0", body, hashlib.sha256).hexdigest()
    assert seen[-1].headers["X-Dati-Signature"] == expected
    payload = json.loads(body)
    assert (payload["type"], payload["alert"]["type"]) == ("alert", "agent_offline")

    # Destino sempre fora do ar: desiste depois do máximo de tentativas e registra o erro.
    await _alert(sessionmaker, t, "agent_offline", "critical", "k-dead")
    limited = test_settings.model_copy(update={"notification_max_attempts": 2})
    async with httpx.AsyncClient(transport=mock_transport([500], [])) as client:
        await _enqueue_and_deliver(sessionmaker, limited, client, now)
        async with sessionmaker() as s:
            await svc.deliver_due(s, limited, client, now + timedelta(minutes=2))
            await s.commit()
    dead = [r for r in await _rows(sessionmaker) if r.status == "failed"]
    assert len(dead) == 1
    assert dead[0].attempts == 2


async def test_whatsapp_meta_and_generic_providers(test_settings: Settings) -> None:
    msg = Message(subject="Coletor sem sinal", body='PC "Recepção" caiu', payload={})
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=mock_transport([200], seen)) as client:
        meta = MetaWhatsAppNotifier(
            test_settings,
            {"phone_number_id": "12345", "access_token": "tok", "api_base": "https://graph.test/v21.0"},
            client,
        )
        await meta.send("+55 (21) 99999-0000", msg)
        generic = GenericWhatsAppNotifier(
            {
                "url_template": "https://zapi.test/send?phone={phone}&t={subject}",
                "body_template": '{"phone": "{phone}", "message": "{text}"}',
                "headers": {"Client-Token": "abc"},
            },
            client,
        )
        await generic.send("21 98888-7777", msg)
    first, second = seen
    assert str(first.url) == "https://graph.test/v21.0/12345/messages"
    assert first.headers["Authorization"] == "Bearer tok"
    sent = json.loads(first.content)
    assert (sent["to"], sent["type"]) == ("5521999990000", "text")
    assert sent["text"]["body"].startswith("*Coletor sem sinal*")
    assert str(second.url) == "https://zapi.test/send?phone=21988887777&t=Coletor%20sem%20sinal"
    assert second.headers["Client-Token"] == "abc"
    assert json.loads(second.content) == {
        "phone": "21988887777",
        "message": 'Coletor sem sinal\nPC "Recepção" caiu',
    }

    with pytest.raises(NotifyError, match="telefone inválido"):
        normalize_phone("1234")
    with pytest.raises(NotifyError, match="phone_number_id"):
        MetaWhatsAppNotifier(test_settings, {}, httpx.AsyncClient())
    async with httpx.AsyncClient(transport=mock_transport([200], [])) as client:
        broken = GenericWhatsAppNotifier(
            {"url_template": "https://zapi.test/x", "body_template": "{sem json"}, client
        )
        with pytest.raises(NotifyError, match="JSON válido"):
            await broken.send("21988887777", msg)


async def test_channel_api_masks_secrets_and_test_send(
    client: httpx.AsyncClient, factory: Factory, api_app: FastAPI
) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    body = {
        "kind": "whatsapp",
        "name": "Plantão",
        "recipients": ["21 99999-0000"],
        "config": {
            "provider": "meta",
            "phone_number_id": "555",
            "access_token": "segredo-real",
            "api_base": "https://graph.test/v21.0",
        },
    }
    created = await client.post("/api/v1/notification-channels", json=body, headers=auth(admin))
    assert created.status_code == 201, created.text
    ch = created.json()
    assert ch["config"]["access_token"] == "••••"
    assert "segredo-real" not in created.text
    renamed = await client.patch(
        f"/api/v1/notification-channels/{ch['id']}",
        json={"name": "Plantão 24h", "config": {"access_token": "••••", "phone_number_id": "556"}},
        headers=auth(admin),
    )
    assert renamed.json()["config"]["phone_number_id"] == "556"
    bad = await client.post(
        "/api/v1/notification-channels",
        json={"kind": "email", "name": "Sem destino", "recipients": []},
        headers=auth(admin),
    )
    assert bad.status_code == 400

    seen: list[httpx.Request] = []

    async def fake_client() -> Any:
        async with httpx.AsyncClient(transport=mock_transport([200], seen)) as c:
            yield c

    api_app.dependency_overrides[http_client] = fake_client
    try:
        tested = await client.post(f"/api/v1/notification-channels/{ch['id']}/test", headers=auth(admin))
    finally:
        api_app.dependency_overrides.pop(http_client)
    assert tested.json() == {"ok": True, "results": [{"destination": "21 99999-0000", "ok": True}]}
    # O segredo guardado continuou o original (a máscara não o sobrescreveu).
    assert seen[0].headers["Authorization"] == "Bearer segredo-real"
    assert str(seen[0].url) == "https://graph.test/v21.0/556/messages"
    log = (await client.get("/api/v1/notifications", headers=auth(admin))).json()
    assert [(n["status"], n["kind"]) for n in log["items"]] == [("sent", "whatsapp")]

    _, op_email = await factory.user(t.reseller_id, role="operator")
    op = await login(client, op_email)
    assert (
        await client.post(f"/api/v1/notification-channels/{ch['id']}/test", headers=auth(op))
    ).status_code == 403
    assert (await client.get("/api/v1/notification-channels", headers=auth(op))).status_code == 200

    # Configurações: silêncio e limiar de troca (a ingestão lê a chave própria).
    s = (await client.get("/api/v1/notification-settings", headers=auth(admin))).json()
    assert s == NotificationSettings().model_dump()
    s["quiet_hours"]["start_hour"] = 21
    s["replacement_threshold_points"] = 30
    saved = await client.put("/api/v1/notification-settings", json=s, headers=auth(admin))
    assert saved.json()["quiet_hours"]["start_hour"] == 21


def test_quiet_window_edges() -> None:
    cfg = NotificationSettings()

    def at(h: int, m: int = 0) -> datetime:
        return datetime(2026, 9, 29, h, m, tzinfo=SP).astimezone(UTC)

    assert svc.quiet_until(at(21, 59), cfg) is None
    assert svc.quiet_until(at(22, 0), cfg) == datetime(2026, 9, 30, 7, 0, tzinfo=SP).astimezone(UTC)
    assert svc.quiet_until(at(3, 0), cfg) == datetime(2026, 9, 29, 7, 0, tzinfo=SP).astimezone(UTC)
    assert svc.quiet_until(at(7, 0), cfg) is None
    day = NotificationSettings.model_validate({"quiet_hours": {"start_hour": 12, "end_hour": 14}})
    assert svc.quiet_until(at(13, 0), day) == datetime(2026, 9, 29, 14, 0, tzinfo=SP).astimezone(UTC)
    off = NotificationSettings.model_validate({"quiet_hours": {"enabled": False}})
    assert svc.quiet_until(at(23, 0), off) is None


async def test_daily_summary(
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    mail_catcher: Any,
) -> None:
    t = await factory.tenant()
    await _channel(
        sessionmaker, test_settings, t, ChannelIn(kind="email", name="Gerência", recipients=["g@c.test"])
    )
    await _channel(
        sessionmaker,
        test_settings,
        t,
        ChannelIn(kind="email", name="Plantão", recipients=["p@c.test"], config={"daily_summary": False}),
    )
    await _alert(sessionmaker, t, "agent_offline", "critical", "k-sum")
    now = datetime(2026, 9, 30, 7, 0, tzinfo=SP).astimezone(UTC)
    async with sessionmaker() as s, httpx.AsyncClient() as client:
        assert await svc.daily_summary(s, test_settings, now) == 1
        await svc.deliver_due(s, test_settings, client, now)
        await s.commit()
    mail = next(m for m in mail_catcher.store.list() if m["to"] == ["g@c.test"])
    assert mail["subject"] == "[Dati Monitor] Resumo diário de 30/09/2026 — Revenda A"
    assert "Alertas abertos: 1 (críticos 1, atenção 0, avisos 0)" in mail["text"]
    assert "Coletores offline: 0" in mail["text"]
