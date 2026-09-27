"""/api/v1/auth — login, refresh (httpOnly cookie + CSRF), logout, password and TOTP."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Request, Response, status

from app.api.deps import LimitedPrincipalDep, SessionDep, SettingsDep, client_ip
from app.core.config import Settings
from app.core.errors import AppError, forbidden, unauthorized
from app.core.permissions import ROLE_BY_CODE, ROLE_PERMISSIONS
from app.core.ratelimit import RateLimiter
from app.core.security import constant_time_equals
from app.models import Reseller, User
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    PreferencesUpdate,
    ResetPasswordRequest,
    SessionResponse,
    TotpCodeRequest,
    TotpDisableRequest,
    TotpSetupResponse,
)
from app.schemas.common import ERROR_RESPONSES, OkResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["autenticação"], responses=ERROR_RESPONSES)

REFRESH_COOKIE = "dm_refresh"
CSRF_COOKIE = "dm_csrf"
CSRF_HEADER = "X-CSRF-Token"
REFRESH_PATH = "/api/v1/auth"


def _limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.login_limiter
    return limiter


def _check_rate(request: Request, key: str) -> None:
    limiter = _limiter(request)
    if not limiter.hit(key):
        raise AppError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            "Muitas tentativas. Aguarde um pouco e tente novamente.",
            retry_after_seconds=round(limiter.retry_after(key)),
        )


def me_payload(user: User, reseller: Reseller) -> MeResponse:
    return MeResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role_code,
        role_name=ROLE_BY_CODE[user.role_code].name,
        reseller_id=user.reseller_id,
        reseller_name=reseller.name,
        customer_id=user.customer_id,
        permissions=sorted(ROLE_PERMISSIONS[user.role_code]),
        totp_enabled=user.totp_enabled,
        must_change_password=user.must_change_password,
        preferences=user.preferences,
        last_login_at=user.last_login_at,
    )


def _set_session_cookies(response: Response, settings: Settings, refresh_token: str | None) -> None:
    if refresh_token is None:
        return
    max_age = settings.refresh_token_days * 86400
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=REFRESH_PATH,
    )
    # Double-submit: o portal lê este cookie e reenvia no cabeçalho X-CSRF-Token.
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(24),
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_PATH, secure=settings.cookie_secure, samesite="strict"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=settings.cookie_secure, samesite="strict")


def _check_csrf(csrf_cookie: str | None, csrf_header: str | None) -> None:
    if not csrf_cookie or not csrf_header or not constant_time_equals(csrf_cookie, csrf_header):
        raise forbidden("Falha na verificação CSRF")


def _token_response(result: auth_service.Session) -> SessionResponse:
    return SessionResponse(
        access_token=result.access_token,
        expires_at=result.access_expires_at,
        limited=result.limited,
        user=me_payload(result.user, result.reseller),
    )


@router.post("/login", response_model=SessionResponse, summary="Entrar")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> SessionResponse:
    ip = client_ip(request)
    _check_rate(request, f"login:ip:{ip}")
    result = await auth_service.login(
        session,
        settings,
        email=body.email,
        password=body.password,
        totp_code=body.totp_code,
        ip=ip,
        user_agent=user_agent,
    )
    await session.commit()
    _set_session_cookies(response, settings, result.refresh_token)
    return _token_response(result)


@router.post("/refresh", response_model=SessionResponse, summary="Renovar a sessão (cookie + CSRF)")
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    dm_refresh: Annotated[str | None, Cookie()] = None,
    dm_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
    user_agent: Annotated[str | None, Header()] = None,
) -> SessionResponse:
    _check_csrf(dm_csrf, x_csrf_token)
    if not dm_refresh:
        raise unauthorized("refresh_missing", "Sessão expirada. Entre novamente.")
    result = await auth_service.refresh(
        session, settings, raw_token=dm_refresh, ip=client_ip(request), user_agent=user_agent
    )
    await session.commit()
    _set_session_cookies(response, settings, result.refresh_token)
    return _token_response(result)


@router.post("/logout", response_model=OkResponse, summary="Sair")
async def logout(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    dm_refresh: Annotated[str | None, Cookie()] = None,
    dm_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> OkResponse:
    _check_csrf(dm_csrf, x_csrf_token)
    await auth_service.logout(session, raw_token=dm_refresh)
    await session.commit()
    _clear_session_cookies(response, settings)
    return OkResponse()


@router.get("/me", response_model=MeResponse, summary="Usuário atual")
async def me(principal: LimitedPrincipalDep, session: SessionDep) -> MeResponse:
    user = await session.get(User, principal.user_id)
    reseller = await session.get(Reseller, principal.reseller_id)
    if user is None or reseller is None:
        raise unauthorized()
    return me_payload(user, reseller)


@router.patch("/me/preferences", response_model=MeResponse, summary="Salvar preferências da interface")
async def update_preferences(
    body: PreferencesUpdate, principal: LimitedPrincipalDep, session: SessionDep
) -> MeResponse:
    user = await session.get(User, principal.user_id)
    reseller = await session.get(Reseller, principal.reseller_id)
    if user is None or reseller is None:
        raise unauthorized()
    user.preferences = {**user.preferences, **body.preferences}
    await session.commit()
    return me_payload(user, reseller)


@router.post("/change-password", response_model=SessionResponse, summary="Trocar a senha")
async def change_password(
    body: ChangePasswordRequest,
    response: Response,
    principal: LimitedPrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> SessionResponse:
    result = await auth_service.change_password(
        session,
        settings,
        principal,
        current_password=body.current_password,
        new_password=body.new_password,
        user_agent=user_agent,
    )
    await session.commit()
    _set_session_cookies(response, settings, result.refresh_token)
    return _token_response(result)


@router.post(
    "/forgot-password",
    response_model=OkResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Pedir e-mail de redefinição de senha",
)
async def forgot_password(
    body: ForgotPasswordRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> OkResponse:
    ip = client_ip(request)
    _check_rate(request, f"forgot:ip:{ip}")
    await auth_service.forgot_password(session, settings, email=body.email, ip=ip)
    await session.commit()
    return OkResponse()


@router.post("/reset-password", response_model=OkResponse, summary="Redefinir a senha com o link do e-mail")
async def reset_password(body: ResetPasswordRequest, request: Request, session: SessionDep) -> OkResponse:
    ip = client_ip(request)
    _check_rate(request, f"reset:ip:{ip}")
    await auth_service.reset_password(session, raw_token=body.token, new_password=body.new_password, ip=ip)
    await session.commit()
    return OkResponse()


@router.post("/totp/setup", response_model=TotpSetupResponse, summary="Gerar segredo do autenticador")
async def totp_setup(
    principal: LimitedPrincipalDep, session: SessionDep, settings: SettingsDep
) -> TotpSetupResponse:
    secret, uri = await auth_service.totp_setup(session, settings, principal)
    await session.commit()
    return TotpSetupResponse(secret=secret, otpauth_uri=uri)


@router.post("/totp/enable", response_model=SessionResponse, summary="Ativar o autenticador")
async def totp_enable(
    body: TotpCodeRequest,
    response: Response,
    principal: LimitedPrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> SessionResponse:
    result = await auth_service.totp_enable(
        session, settings, principal, code=body.code, user_agent=user_agent
    )
    await session.commit()
    _set_session_cookies(response, settings, result.refresh_token)
    return _token_response(result)


@router.post("/totp/disable", response_model=OkResponse, summary="Desativar o autenticador")
async def totp_disable(
    body: TotpDisableRequest, principal: LimitedPrincipalDep, session: SessionDep, settings: SettingsDep
) -> OkResponse:
    if principal.limited:
        raise forbidden("Conclua a configuração pendente antes")
    await auth_service.totp_disable(session, settings, principal, password=body.password, code=body.code)
    await session.commit()
    return OkResponse()
