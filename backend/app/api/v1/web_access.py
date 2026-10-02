"""/api/v1 acesso à página web da impressora (PROMPT 4.9)."""

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.schemas.common import ERROR_RESPONSES
from app.schemas.web_access import WebSessionByIpIn, WebSessionIn, WebSessionOut
from app.services import web_access as svc

router = APIRouter(responses=ERROR_RESPONSES, tags=["acesso web"])


@router.post(
    "/devices/{device_id}/web-session",
    response_model=WebSessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir a página web da impressora (túnel pelo coletor)",
)
async def open_device(
    device_id: uuid.UUID, body: WebSessionIn, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> WebSessionOut:
    out = await svc.open_for_device(session, settings, p, device_id, port=body.port, scheme=body.scheme)
    await session.commit()
    return out


@router.post(
    "/sites/{site_id}/web-session",
    response_model=WebSessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir pelo IP (só impressoras cadastradas no local; recusa é auditada)",
)
async def open_ip(
    site_id: uuid.UUID, body: WebSessionByIpIn, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> WebSessionOut:
    out = await svc.open_by_ip(session, settings, p, site_id, ip=body.ip, port=body.port, scheme=body.scheme)
    await session.commit()
    return out


@router.post(
    "/web-sessions/{session_id}/close", status_code=status.HTTP_204_NO_CONTENT, summary="Encerrar a sessão"
)
async def close(session_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await svc.close(session, p, session_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
