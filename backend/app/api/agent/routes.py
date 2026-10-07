"""/api/agent — endpoints used by dm-agent (enrollment, token, heartbeat, config, readings, command
contingency channel and uploads). The WebSocket channel lives in the gateway process."""

import gzip
import uuid
import zlib
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse

from app.api.deps import SessionDep, SettingsDep, client_ip
from app.core.errors import AppError, bad_request
from app.core.ratelimit import RateLimiter
from app.models import Agent
from app.schemas import agent as proto
from app.schemas.common import ERROR_RESPONSES, OkResponse
from app.services import agents as svc
from app.services import commands as commands_svc
from app.services import releases as releases_svc
from app.services import watchdog as watchdog_svc
from app.services.ingest import ingest_batch, parse_batch

router = APIRouter(prefix="/api/agent", tags=["coletores (protocolo)"], responses=ERROR_RESPONSES)

MAX_BODY = 16 * 1024 * 1024  # 16 MB descompactados (500 itens com extras cabem folgados)


def _limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.agent_limiter
    return limiter


def _rate(request: Request, key: str) -> None:
    if not _limiter(request).hit(key):
        raise AppError(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", "Muitas requisições; aguarde")


async def current_agent(request: Request, session: SessionDep, settings: SettingsDep) -> Agent:
    agent = await svc.authenticate(session, settings, request.headers.get("Authorization", ""))
    _rate(request, f"agent:{agent.id}")
    return agent


AgentDep = Annotated[Agent, Depends(current_agent)]


async def read_json_body(request: Request) -> bytes:
    raw = await request.body()
    if request.headers.get("Content-Encoding", "").lower() == "gzip":
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            raw = d.decompress(raw, MAX_BODY + 1)
            if len(raw) > MAX_BODY or d.unconsumed_tail:
                raise AppError(status.HTTP_413_CONTENT_TOO_LARGE, "body_too_large", "Lote grande demais")
        except (zlib.error, gzip.BadGzipFile) as exc:
            raise bad_request("invalid_gzip", "Corpo gzip inválido") from exc
    elif len(raw) > MAX_BODY:
        raise AppError(status.HTTP_413_CONTENT_TOO_LARGE, "body_too_large", "Lote grande demais")
    return raw


@router.post(
    "/enroll", response_model=proto.EnrollResponse, summary="Cadastrar coletor com o código do portal"
)
async def enroll(
    body: proto.EnrollRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> proto.EnrollResponse:
    ip = client_ip(request)
    _rate(request, f"enroll:{ip}")
    resp = await svc.enroll(session, settings, body, ip)
    await session.commit()
    return resp


@router.post(
    "/enroll/check",
    response_model=proto.EnrollCheckResponse,
    summary="Conferir o código de cadastro sem usá-lo (instalador)",
)
async def enroll_check(
    body: proto.EnrollCheckRequest, request: Request, session: SessionDep
) -> proto.EnrollCheckResponse:
    _rate(request, f"enroll:{client_ip(request)}")
    return await svc.check_enrollment_code(session, body.code)


@router.post("/token", response_model=proto.TokenResponse, summary="Token de sessão (HMAC do segredo)")
async def token(
    body: proto.TokenRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> proto.TokenResponse:
    _rate(request, f"token:{client_ip(request)}")
    nonces: svc.NonceCache = request.app.state.agent_nonces
    return await svc.issue_token(session, settings, body, nonces)


@router.post("/heartbeat", response_model=proto.HeartbeatResponse, summary="Heartbeat pelo canal HTTPS")
async def heartbeat(
    request: Request, body: proto.HeartbeatRequest, agent: AgentDep, session: SessionDep
) -> proto.HeartbeatResponse:
    resp = await svc.heartbeat(session, agent, body, channel="http", ip=client_ip(request))
    await session.commit()
    return resp


@router.get("/config", response_model=proto.AgentConfig, summary="Configuração do local do coletor")
async def config(agent: AgentDep, session: SessionDep, settings: SettingsDep) -> proto.AgentConfig:
    return await svc.agent_config(session, settings, agent)


@router.post(
    "/uninstalling", response_model=OkResponse, summary="Aviso do desinstalador: o coletor está saindo do PC"
)
async def uninstalling(body: proto.UninstallNotice, agent: AgentDep, session: SessionDep) -> OkResponse:
    await svc.uninstalling(session, agent, body)
    await session.commit()
    return OkResponse()


@router.post(
    "/ranges/suggest", response_model=OkResponse, summary="Sugerir sub-redes para aprovação no portal"
)
async def suggest(body: proto.SuggestRangesRequest, agent: AgentDep, session: SessionDep) -> OkResponse:
    await svc.suggest_ranges(session, agent, body)
    await session.commit()
    return OkResponse()


@router.post(
    "/readings",
    response_model=proto.ReadingsResponse,
    summary="Lote de leituras (gzip, até 500 itens, idempotente por chave)",
    openapi_extra={
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReadingsRequest"}}}
        }
    },
)
async def readings(request: Request, agent: AgentDep, session: SessionDep) -> proto.ReadingsResponse:
    raw = await read_json_body(request)
    body, rejected = parse_batch(raw, agent.id)
    resp = await ingest_batch(session, agent, body)
    await session.commit()
    if rejected:
        # Item inválido é recusado sozinho (o coletor o move para dead_letter); os outros entram.
        resp = proto.ReadingsResponse(results=[*rejected, *resp.results])
    return resp


# ----------------------------------------------------------------------------- comandos (contingência HTTPS)


@router.get(
    "/commands/pending",
    response_model=proto.PendingCommandsResponse,
    summary="Comandos pendentes (canal de contingência quando o WebSocket está caído)",
)
async def pending_commands(agent: AgentDep, session: SessionDep) -> proto.PendingCommandsResponse:
    cmds = await commands_svc.claim_for_delivery(session, agent.id)
    await session.commit()
    return proto.PendingCommandsResponse(commands=[commands_svc.to_message(c) for c in cmds])


@router.post(
    "/commands/{command_id}/update",
    response_model=proto.CommandUpdateResponse,
    summary="Andamento/resultado de um comando (idempotente)",
)
async def command_update(
    command_id: str, body: proto.CommandUpdate, agent: AgentDep, session: SessionDep
) -> proto.CommandUpdateResponse:
    if body.id != command_id:
        raise bad_request("id_mismatch", "O id do corpo difere do id da URL")
    cmd = await commands_svc.apply_update(session, agent, body)
    await session.commit()
    return proto.CommandUpdateResponse.model_validate({"id": str(cmd.id), "state": cmd.state})


async def _upload_body(request: Request, limit: int) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise AppError(status.HTTP_413_CONTENT_TOO_LARGE, "upload_too_large", "Arquivo grande demais")
    return bytes(data)


@router.post(
    "/uploads/logs",
    response_model=proto.UploadResponse,
    summary="Logs compactados (.zip) pedidos por get_logs",
    openapi_extra={"requestBody": {"content": {"application/zip": {}}}},
)
async def upload_logs(
    command_id: uuid.UUID, request: Request, agent: AgentDep, session: SessionDep, settings: SettingsDep
) -> proto.UploadResponse:
    data = await _upload_body(request, commands_svc.MAX_LOG_UPLOAD)
    row = await commands_svc.store_logs(session, settings, agent, command_id, data)
    await session.commit()
    return proto.UploadResponse(id=str(row.id), size_bytes=row.size_bytes)


@router.post(
    "/uploads/mib-walk",
    response_model=proto.UploadResponse,
    summary="Walk SNMP (.snmprec em gzip) pedido por mib_walk",
    openapi_extra={"requestBody": {"content": {"application/gzip": {}}}},
)
async def upload_walk(
    command_id: uuid.UUID, request: Request, agent: AgentDep, session: SessionDep, settings: SettingsDep
) -> proto.UploadResponse:
    data = await _upload_body(request, commands_svc.MAX_WALK_UPLOAD)
    row = await commands_svc.store_walk(session, settings, agent, command_id, data)
    await session.commit()
    return proto.UploadResponse(id=str(row.id), size_bytes=len(data))


# ----------------------------------------------------------------------------- releases (download)


@router.get(
    "/releases/{release_id}/file",
    response_class=FileResponse,
    summary="Binário de uma versão (para o watchdog/coletor que executa um update)",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def release_file(release_id: uuid.UUID, agent: AgentDep, session: SessionDep) -> FileResponse:
    path = await releases_svc.get_release_file(session, agent, release_id)
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


# ----------------------------------------------------------------------------- watchdog (seção 5.1)

watchdog_router = APIRouter(prefix="/api/watchdog", tags=["watchdog (protocolo)"], responses=ERROR_RESPONSES)


@watchdog_router.post(
    "/heartbeat",
    response_model=proto.WatchdogHeartbeatResponse,
    summary="Heartbeat do dm-watchdog (a cada 60 s); devolve os comandos do watchdog",
)
async def watchdog_heartbeat(
    body: proto.WatchdogHeartbeatRequest, agent: AgentDep, session: SessionDep
) -> proto.WatchdogHeartbeatResponse:
    resp = await watchdog_svc.heartbeat(session, agent, body)
    await session.commit()
    return resp
