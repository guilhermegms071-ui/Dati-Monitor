"""/api/v1 remote commands (PROMPT 4.7), agent log files and MIB walks."""

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, status
from fastapi.responses import FileResponse

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.core.errors import not_found
from app.schemas.commands import COMMAND_LABELS, AgentLogOut, CommandIn, CommandOut, MibWalkOut
from app.schemas.common import ERROR_RESPONSES, Page
from app.services import commands as svc
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Direction

router = APIRouter(responses=ERROR_RESPONSES, tags=["comandos"])
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


@router.get("/command-types", summary="Tipos de comando disponíveis (rótulos em português)")
async def command_types(p: PrincipalDep) -> dict[str, str]:
    p.require("agents.read")
    return COMMAND_LABELS


@router.post(
    "/agents/{agent_id}/commands",
    response_model=CommandOut,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar comando ao coletor",
)
async def create_command(
    agent_id: uuid.UUID, body: CommandIn, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> CommandOut:
    cmd = await svc.create_command(session, settings, p, agent_id, body)
    await session.commit()
    return CommandOut.model_validate(cmd)


@router.get("/agents/{agent_id}/commands", response_model=Page[CommandOut], summary="Comandos do coletor")
async def list_commands(
    agent_id: uuid.UUID,
    p: PrincipalDep,
    session: SessionDep,
    state: str | None = None,
    direction: Direction = "desc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[CommandOut]:
    page = await svc.list_commands(
        session, p, agent_id, state=state, direction=direction, limit=limit, cursor=cursor
    )
    return Page(items=[CommandOut.model_validate(c) for c in page.items], next_cursor=page.next_cursor)


@router.get("/commands/{command_id}", response_model=CommandOut, summary="Estado de um comando")
async def get_command(command_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> CommandOut:
    return CommandOut.model_validate(await svc.get_command(session, p, command_id))


@router.post("/commands/{command_id}/cancel", response_model=CommandOut, summary="Cancelar comando")
async def cancel_command(command_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> CommandOut:
    cmd = await svc.cancel_command(session, p, command_id)
    await session.commit()
    return CommandOut.model_validate(cmd)


def _file(path: str, filename: str, media_type: str) -> FileResponse:
    if not Path(path).is_file():
        raise not_found("Arquivo")
    return FileResponse(path, filename=filename, media_type=media_type)


@router.get("/agents/{agent_id}/logs", response_model=list[AgentLogOut], summary="Logs enviados pelo coletor")
async def list_logs(agent_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> list[AgentLogOut]:
    return [AgentLogOut.model_validate(r) for r in await svc.list_agent_logs(session, p, agent_id)]


@router.get("/agent-logs/{log_id}/download", summary="Baixar logs (.zip)", response_class=FileResponse)
async def download_log(log_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> FileResponse:
    row = await svc.get_agent_log(session, p, log_id)
    name = f"logs-coletor-{row.created_at:%Y%m%d-%H%M}.zip"
    return _file(row.file_path, name, "application/zip")


@router.get("/mib-walks", response_model=list[MibWalkOut], summary="Walks SNMP")
async def list_walks(
    p: PrincipalDep,
    session: SessionDep,
    agent_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
) -> list[MibWalkOut]:
    rows = await svc.list_walks(session, p, agent_id=agent_id, device_id=device_id)
    return [MibWalkOut.model_validate(r) for r in rows]


@router.get("/mib-walks/{walk_id}/download", summary="Baixar walk (.snmprec.gz)", response_class=FileResponse)
async def download_walk(walk_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> FileResponse:
    row = await svc.get_walk(session, p, walk_id)
    name = f"walk-{row.ip.replace('.', '-')}-{row.created_at:%Y%m%d-%H%M}.snmprec.gz"
    return _file(row.file_path, name, "application/gzip")
