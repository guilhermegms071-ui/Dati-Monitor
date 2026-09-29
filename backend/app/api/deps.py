"""Central FastAPI dependencies: settings, DB session and the authenticated principal (tenant scope)."""

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import forbidden, unauthorized
from app.core.principal import Principal
from app.core.security import InvalidTokenError, decode_access_token, password_version
from app.models import Reseller, User
from app.services.permissions import effective_permissions


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as session:
        yield session


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _authenticate(
    request: Request, session: AsyncSession, settings: Settings, *, allow_limited: bool
) -> Principal:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized()
    try:
        claims = decode_access_token(token, secret=settings.jwt_secret.get_secret_value())
    except InvalidTokenError as exc:
        raise unauthorized("token_invalid", f"Sessão inválida: {exc}") from exc
    user = await session.get(User, claims.user_id)
    if user is None or user.deleted_at is not None or not user.active:
        raise unauthorized("user_inactive", "Usuário inativo ou removido")
    if claims.password_version != password_version(user.password_changed_at):
        raise unauthorized("token_revoked", "A senha foi alterada; entre novamente")
    reseller = await session.get(Reseller, user.reseller_id)
    if reseller is None or reseller.deleted_at is not None:
        raise unauthorized("reseller_inactive", "A revenda deste usuário está desativada")
    if claims.limited and not allow_limited:
        raise forbidden(
            "Conclua a troca de senha"
            if claims.limited == "password_change_required"
            else "Configure o autenticador"
        )
    # Papel e escopo vêm do banco (não do token): mudanças valem na hora.
    return Principal(
        user_id=user.id,
        reseller_id=user.reseller_id,
        role=user.role_code,
        customer_id=user.customer_id,
        permissions=await effective_permissions(session, user.reseller_id, user.role_code),
        limited=claims.limited,
        email=user.email,
        ip=client_ip(request),
    )


async def get_principal(request: Request, session: SessionDep, settings: SettingsDep) -> Principal:
    return await _authenticate(request, session, settings, allow_limited=False)


async def get_limited_principal(request: Request, session: SessionDep, settings: SettingsDep) -> Principal:
    """Accepts tokens restricted to password change / TOTP setup."""
    return await _authenticate(request, session, settings, allow_limited=True)


PrincipalDep = Annotated[Principal, Depends(get_principal)]
LimitedPrincipalDep = Annotated[Principal, Depends(get_limited_principal)]


def require(permission: str) -> Callable[..., Coroutine[Any, Any, Principal]]:
    async def _dep(principal: PrincipalDep) -> Principal:
        principal.require(permission)
        return principal

    return _dep
