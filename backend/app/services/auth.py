"""Portal authentication: login with lockout and TOTP, rotating refresh tokens, password change/reset."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import Settings
from app.core.errors import AppError, bad_request, unauthorized
from app.core.mail import MailError, send_email
from app.core.permissions import RESELLER_ADMIN, ROLE_BY_CODE, ROLE_PERMISSIONS
from app.core.principal import Principal
from app.core.product import get_product
from app.core.security import (
    LimitedReason,
    create_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    password_needs_rehash,
    verify_password,
)
from app.models import PasswordResetToken, RefreshToken, Reseller, Setting, User
from app.services import audit
from app.services.permissions import effective_permissions

logger = logging.getLogger(__name__)

REFRESH_REUSE_GRACE = timedelta(seconds=10)
REQUIRE_TOTP_ADMINS_KEY = "security.require_totp_for_admins"
_INVALID = "E-mail ou senha incorretos"


@dataclass(frozen=True)
class Session:
    user: User
    reseller: Reseller
    access_token: str
    access_expires_at: datetime
    refresh_token: str | None
    limited: LimitedReason | None
    permissions: frozenset[str]


def _now() -> datetime:
    return datetime.now(UTC)


def _totp_aad(user_id: uuid.UUID) -> bytes:
    return b"totp:" + user_id.bytes


def _verify_totp(settings: Settings, user: User, code: str, now: datetime) -> bool:
    """Accepts the current step ±1 and rejects any step already used (replay)."""
    if not user.totp_secret_enc:
        return False
    secret = crypto.decrypt(
        settings.master_key_bytes, user.totp_secret_enc, associated_data=_totp_aad(user.id)
    )
    totp = pyotp.TOTP(secret)
    current = int(now.timestamp()) // totp.interval
    for step in (current - 1, current, current + 1):
        if user.totp_last_step is not None and step <= user.totp_last_step:
            continue
        if pyotp.utils.strings_equal(totp.generate_otp(step), code):
            user.totp_last_step = step
            return True
    return False


async def _reseller_requires_admin_totp(session: AsyncSession, reseller_id: uuid.UUID) -> bool:
    value = (
        await session.execute(
            select(Setting.value).where(
                Setting.reseller_id == reseller_id, Setting.key == REQUIRE_TOTP_ADMINS_KEY
            )
        )
    ).scalar_one_or_none()
    return bool(value and value.get("enabled"))


async def _limited_reason(session: AsyncSession, user: User) -> LimitedReason | None:
    if user.must_change_password:
        return "password_change_required"
    if (
        user.role_code == RESELLER_ADMIN
        and not user.totp_enabled
        and await _reseller_requires_admin_totp(session, user.reseller_id)
    ):
        return "totp_setup_required"
    return None


async def _issue(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    ip: str | None,
    user_agent: str | None,
    family_id: uuid.UUID | None = None,
) -> tuple[Session, RefreshToken | None]:
    reseller = await session.get(Reseller, user.reseller_id)
    if reseller is None or reseller.deleted_at is not None:
        raise unauthorized("reseller_inactive", "A revenda deste usuário está desativada")
    limited = await _limited_reason(session, user)
    access, expires = create_access_token(
        secret=settings.jwt_secret.get_secret_value(),
        user_id=user.id,
        reseller_id=user.reseller_id,
        role=user.role_code,
        customer_id=user.customer_id,
        minutes=settings.access_token_minutes,
        limited=limited,
        password_changed_at=user.password_changed_at,
    )
    raw_refresh: str | None = None
    token_row: RefreshToken | None = None
    if limited is None:
        raw_refresh = new_opaque_token()
        token_row = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=hash_token(raw_refresh),
            expires_at=_now() + timedelta(days=settings.refresh_token_days),
            created_ip=ip,
            user_agent=(user_agent or "")[:300] or None,
        )
        session.add(token_row)
    return (
        Session(
            user=user,
            reseller=reseller,
            access_token=access,
            access_expires_at=expires,
            refresh_token=raw_refresh,
            limited=limited,
            permissions=await effective_permissions(session, user.reseller_id, user.role_code),
        ),
        token_row,
    )


async def login(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    totp_code: str | None,
    ip: str | None,
    user_agent: str | None,
) -> Session:
    now = _now()
    normalized = email.strip().lower()
    user = (
        await session.execute(
            select(User).where(User.email == normalized, User.deleted_at.is_(None)).with_for_update()
        )
    ).scalar_one_or_none()

    if user is not None and user.locked_until is not None and user.locked_until > now:
        raise AppError(
            423,
            "account_locked",
            "Conta bloqueada temporariamente por excesso de tentativas. Tente novamente mais tarde "
            "ou peça ao administrador para desbloquear.",
            locked_until=user.locked_until.isoformat(),
        )

    password_ok = verify_password(user.password_hash if user else None, password)
    if user is None or not password_ok or not user.active:
        if user is not None and not password_ok:
            await _register_failure(session, settings, user, now, reason="senha incorreta", ip=ip)
        raise unauthorized("invalid_credentials", _INVALID)

    if user.totp_enabled:
        if not totp_code:
            raise unauthorized("totp_required", "Informe o código do aplicativo autenticador")
        if not _verify_totp(settings, user, totp_code, now):
            await _register_failure(session, settings, user, now, reason="código TOTP inválido", ip=ip)
            raise unauthorized("totp_invalid", "Código do autenticador inválido")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    result, _ = await _issue(session, settings, user, ip=ip, user_agent=user_agent)
    await audit.record(
        session,
        _principal_for(user, ip),
        action="auth.login",
        entity="user",
        entity_id=user.id,
        reseller_id=user.reseller_id,
        after={"limited": result.limited},
    )
    return result


async def _register_failure(
    session: AsyncSession, settings: Settings, user: User, now: datetime, *, reason: str, ip: str | None
) -> None:
    """Counts a failed attempt and locks after N failures. Committed immediately (the request then fails)."""
    user.failed_login_count += 1
    locked = user.failed_login_count >= settings.login_max_failures
    if locked:
        user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
        user.failed_login_count = 0
        logger.warning("usuário %s bloqueado após falhas de login (%s)", user.email, reason)
    await audit.record(
        session,
        None,
        action="auth.login_failed",
        entity="user",
        entity_id=user.id,
        reseller_id=user.reseller_id,
        after={"reason": reason, "locked": locked, "ip": ip},
    )
    await session.commit()


def _principal_for(user: User, ip: str | None) -> Principal:
    return Principal(
        user_id=user.id,
        reseller_id=user.reseller_id,
        role=user.role_code,
        customer_id=user.customer_id,
        permissions=ROLE_PERMISSIONS[user.role_code],
        email=user.email,
        ip=ip,
    )


async def refresh(
    session: AsyncSession, settings: Settings, *, raw_token: str, ip: str | None, user_agent: str | None
) -> Session:
    now = _now()
    token = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)).with_for_update()
        )
    ).scalar_one_or_none()
    if token is None:
        raise unauthorized("refresh_invalid", "Sessão inválida. Entre novamente.")
    if token.revoked_at is not None:
        if token.replaced_by_id is not None and now - token.revoked_at < REFRESH_REUSE_GRACE:
            # Duas abas renovando ao mesmo tempo: não é roubo; o cliente deve usar o token mais novo.
            raise AppError(409, "refresh_in_progress", "Sessão já renovada por outra aba. Tente novamente.")
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        logger.warning("reuso de refresh token detectado (usuário %s): sessões revogadas", token.user_id)
        await session.commit()
        raise unauthorized("refresh_reused", "Sessão encerrada por segurança. Entre novamente.")
    if token.expires_at <= now:
        raise unauthorized("refresh_expired", "Sessão expirada. Entre novamente.")
    user = await session.get(User, token.user_id)
    if user is None or not user.active or user.deleted_at is not None:
        raise unauthorized("refresh_invalid", "Sessão inválida. Entre novamente.")
    result, new_row = await _issue(
        session, settings, user, ip=ip, user_agent=user_agent, family_id=token.family_id
    )
    token.revoked_at = now
    token.replaced_by_id = new_row.id if new_row else None
    return result


async def logout(session: AsyncSession, *, raw_token: str | None) -> None:
    if not raw_token:
        return
    token = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    ).scalar_one_or_none()
    if token is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )


async def _revoke_all_refresh(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


def _set_password(user: User, new_password: str) -> None:
    if user.email and new_password.strip().lower() == user.email:
        raise bad_request("weak_password", "A senha não pode ser igual ao e-mail")
    user.password_hash = hash_password(new_password)
    # Muda a versão da senha embutida nos tokens: os emitidos antes deixam de valer.
    user.password_changed_at = _now()
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None


async def change_password(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    *,
    current_password: str,
    new_password: str,
    user_agent: str | None,
) -> Session:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise unauthorized()
    if not verify_password(user.password_hash, current_password):
        raise bad_request("invalid_current_password", "Senha atual incorreta")
    if verify_password(user.password_hash, new_password):
        raise bad_request("same_password", "A nova senha precisa ser diferente da atual")
    _set_password(user, new_password)
    await _revoke_all_refresh(session, user.id)
    await audit.record(
        session,
        principal,
        action="auth.change_password",
        entity="user",
        entity_id=user.id,
        reseller_id=user.reseller_id,
    )
    result, _ = await _issue(session, settings, user, ip=principal.ip, user_agent=user_agent)
    return result


async def forgot_password(session: AsyncSession, settings: Settings, *, email: str, ip: str | None) -> None:
    """Always succeeds for the caller (no user enumeration); the e-mail only goes to existing active users."""
    user = (
        await session.execute(
            select(User).where(
                User.email == email.strip().lower(), User.deleted_at.is_(None), User.active.is_(True)
            )
        )
    ).scalar_one_or_none()
    if user is None:
        logger.info("pedido de redefinição para e-mail inexistente (ip=%s)", ip)
        return
    try:
        await send_reset_email(session, settings, user)
    except AppError:
        # Responder com erro revelaria que o e-mail existe; a falha fica registrada como ERRO no log
        # (send_email já registra a causa) para a equipe agir.
        logger.error("redefinição de senha: e-mail não enviado para o usuário %s", user.id)
    await audit.record(
        session,
        None,
        action="auth.forgot_password",
        entity="user",
        entity_id=user.id,
        reseller_id=user.reseller_id,
        after={"ip": ip},
    )


async def send_reset_email(session: AsyncSession, settings: Settings, user: User) -> None:
    now = _now()
    await session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw = new_opaque_token()
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=now + timedelta(minutes=settings.password_reset_minutes),
        )
    )
    link = f"{settings.public_base_url.rstrip('/')}/redefinir-senha?token={raw}"
    product = get_product().name
    try:
        await send_email(
            settings,
            to=[user.email],
            subject=f"{product}: redefinição de senha",
            body=(
                f"Olá, {user.name}.\n\nRecebemos um pedido para redefinir sua senha no {product}.\n"
                f"Abra o link abaixo (válido por {settings.password_reset_minutes} minutos):\n\n{link}\n\n"
                "Se você não pediu, ignore este e-mail; sua senha continua a mesma."
            ),
        )
    except MailError as exc:
        raise AppError(
            502, "mail_failed", "Não foi possível enviar o e-mail de redefinição. Tente mais tarde."
        ) from exc


async def reset_password(session: AsyncSession, *, raw_token: str, new_password: str, ip: str | None) -> None:
    now = _now()
    token = (
        await session.execute(
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == hash_token(raw_token))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise bad_request("reset_token_invalid", "Link de redefinição inválido ou expirado. Peça um novo.")
    user = await session.get(User, token.user_id)
    if user is None or user.deleted_at is not None or not user.active:
        raise bad_request("reset_token_invalid", "Link de redefinição inválido ou expirado. Peça um novo.")
    _set_password(user, new_password)
    token.used_at = now
    await _revoke_all_refresh(session, user.id)
    await audit.record(
        session,
        None,
        action="auth.reset_password",
        entity="user",
        entity_id=user.id,
        reseller_id=user.reseller_id,
        after={"ip": ip},
    )


async def totp_setup(session: AsyncSession, settings: Settings, principal: Principal) -> tuple[str, str]:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise unauthorized()
    if user.totp_enabled:
        raise bad_request(
            "totp_already_enabled", "O autenticador já está ativo. Desative antes de configurar outro."
        )
    secret = pyotp.random_base32()
    user.totp_secret_enc = crypto.encrypt(
        settings.master_key_bytes, secret, associated_data=_totp_aad(user.id)
    )
    user.totp_last_step = None
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=get_product().name)
    return secret, uri


async def totp_enable(
    session: AsyncSession, settings: Settings, principal: Principal, *, code: str, user_agent: str | None
) -> Session:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise unauthorized()
    if user.totp_enabled:
        raise bad_request("totp_already_enabled", "O autenticador já está ativo")
    if not user.totp_secret_enc:
        raise bad_request("totp_not_setup", "Gere o QR code antes de ativar")
    if not _verify_totp(settings, user, code, _now()):
        raise bad_request("totp_invalid", "Código do autenticador inválido")
    user.totp_enabled = True
    await audit.record(
        session,
        principal,
        action="auth.totp_enable",
        entity="user",
        entity_id=user.id,
        reseller_id=user.reseller_id,
    )
    result, _ = await _issue(session, settings, user, ip=principal.ip, user_agent=user_agent)
    return result


async def totp_disable(
    session: AsyncSession, settings: Settings, principal: Principal, *, password: str, code: str
) -> None:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise unauthorized()
    if not user.totp_enabled:
        raise bad_request("totp_not_enabled", "O autenticador não está ativo")
    if not verify_password(user.password_hash, password) or not _verify_totp(settings, user, code, _now()):
        raise bad_request("totp_invalid", "Senha ou código do autenticador inválidos")
    if user.role_code == RESELLER_ADMIN and await _reseller_requires_admin_totp(session, user.reseller_id):
        raise bad_request("totp_required_by_policy", "A revenda exige autenticador para administradores")
    user.totp_enabled = False
    user.totp_secret_enc = None
    user.totp_last_step = None
    await audit.record(
        session,
        principal,
        action="auth.totp_disable",
        entity="user",
        entity_id=user.id,
        reseller_id=user.reseller_id,
    )


def role_name(code: str) -> str:
    return ROLE_BY_CODE[code].name
