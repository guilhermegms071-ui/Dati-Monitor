"""Remote commands (PROMPT 4.7): creation from the portal, delivery to the agent (WebSocket or HTTPS
polling), progress/result updates, cancellation, expiry and the files agents upload (logs, walks).

Lifecycle: pending → sent → acked → running → succeeded | failed, plus expired / cancelled. Final
states never change again; every update from the agent is idempotent by command id.
"""

import gzip
import io
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.notify import CH_COMMAND, CH_COMMAND_UPDATE, notify, notify_event
from app.core.permissions import RESELLER_ADMIN, role_level
from app.core.principal import Principal
from app.core.security import set_server_signature
from app.models import Agent, AgentLog, ClusterEvent, Command, Device, IpRange, MibWalk, Site
from app.schemas import agent as proto
from app.schemas.commands import COMMAND_LABELS, PARAMS_BY_TYPE, CommandIn, command_target
from app.services import agents as agents_svc
from app.services import audit, releases
from app.services.pagination import Direction, PageResult, SortOption, paginate

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"succeeded", "failed", "expired", "cancelled"})
OUTPUT_LIMIT = 1024 * 1024  # 1 MB (seção 3: saída limitada a 1 MB)
# Comando entregue e não confirmado é reenviado depois disto (o coletor ignora repetidos pelo id).
RESEND_UNACKED_AFTER = timedelta(seconds=20)
# Comando em execução sem notícia por tanto tempo depois de expirar é dado como falho.
RUNNING_GRACE = timedelta(hours=1)
MAX_LOG_UPLOAD = 50 * 1024 * 1024
MAX_WALK_UPLOAD = 64 * 1024 * 1024
MAX_WALK_UNCOMPRESSED = 512 * 1024 * 1024
_STEP = {"pending": 0, "sent": 1, "acked": 2, "running": 3}


def _now() -> datetime:
    return datetime.now(UTC)


# ----------------------------------------------------------------------------- portal


async def create_command(
    session: AsyncSession, settings: Settings, p: Principal, agent_id: uuid.UUID, data: CommandIn
) -> Command:
    p.require("agents.command")
    agent = await agents_svc.get_agent(session, p, agent_id)
    if agent.revoked_at is not None:
        raise conflict("agent_revoked", "Coletor revogado: não recebe comandos")
    if agent.enrolled_at is None:
        raise conflict("agent_not_enrolled", "Coletor ainda não foi instalado/cadastrado no PC do cliente")
    try:
        params = PARAMS_BY_TYPE[data.type].model_validate(data.params)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise bad_request(
            "invalid_params", f"Parâmetros inválidos para {COMMAND_LABELS[data.type]}", errors=errors
        ) from exc
    raw = params.model_dump(mode="json")
    target = command_target(data.type, raw)
    if target == "watchdog" and agent.last_watchdog_seen_at is None:
        raise conflict(
            "watchdog_never_seen",
            "O vigia (dm-watchdog) deste PC nunca se comunicou: instale o serviço DatiMonitorWatchdog",
        )
    stored = await _prepare(session, p, agent, data.type, raw)
    if data.type == "set_server":
        p.require("agents.admin")  # muda para onde o coletor manda tudo: só quem administra coletores
        stored = sign_set_server(settings, agent, stored)
    minutes = data.expires_in_minutes or settings.command_expiry_minutes
    cmd = await insert_command(
        session, p, agent, data.type, stored, target=target, expires_in=timedelta(minutes=minutes)
    )
    if data.type in ("pause", "resume", "promote_master"):
        await agents_svc.emit_state(session, agent)
    return cmd


async def insert_command(
    session: AsyncSession,
    p: Principal | None,
    agent: Agent,
    ctype: str,
    stored: dict[str, Any],
    *,
    target: str,
    expires_in: timedelta,
    reason: str | None = None,
) -> Command:
    """Stores a command already validated/prepared. `p=None` = created by the server itself (cluster
    failover, automatic update), audited without a user and with the reason."""
    cmd = Command(
        reseller_id=agent.reseller_id,
        agent_id=agent.id,
        target=target,
        type=ctype,
        params=stored,
        state="pending",
        created_by=p.user_id if p else None,
        expires_at=_now() + expires_in,
    )
    session.add(cmd)
    await session.flush()
    after: dict[str, Any] = {"agent_id": str(agent.id), "type": ctype, "target": target, "params": stored}
    if reason:
        after["reason"] = reason
    await audit.record(
        session,
        p,
        action=f"command.{ctype}",
        entity="command",
        entity_id=cmd.id,
        reseller_id=agent.reseller_id,
        after=after,
    )
    if target == "agent":
        await notify(session, CH_COMMAND, str(agent.id))  # o gateway entrega na hora pelo WebSocket
    await emit_commands(session, [cmd])
    return cmd


async def _customers(session: AsyncSession, agent_ids: set[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    if not agent_ids:
        return {}
    rows = await session.execute(
        select(Agent.id, Site.customer_id).join(Site, Site.id == Agent.site_id).where(Agent.id.in_(agent_ids))
    )
    return dict(rows.tuples().all())


async def emit_commands(session: AsyncSession, cmds: list[Command]) -> None:
    """Live event (portal SSE) for each command whose state changed."""
    customers = await _customers(session, {c.agent_id for c in cmds})
    for c in cmds:
        await notify_event(
            session,
            CH_COMMAND_UPDATE,
            "command",
            reseller_id=c.reseller_id,
            customer_id=customers.get(c.agent_id),
            id=c.id,
            agent_id=c.agent_id,
            state=c.state,
        )


def sign_set_server(settings: Settings, agent: Agent, params: dict[str, Any]) -> dict[str, Any]:
    """Assina o novo endereço com a chave do coletor. O coletor confere a assinatura e, antes de trocar,
    se autentica no novo servidor com a própria credencial: só uma hospedagem com a mesma base de coletores
    passa, então nem quem tem acesso ao portal desvia coletores para outro servidor."""
    server_url = params["server_url"]
    ws_url = params.get("ws_url") or ""
    issued_at = int(datetime.now(UTC).timestamp())
    key = agents_svc.agent_key(settings, agent)
    signature = set_server_signature(key, str(agent.id), server_url, ws_url, issued_at)
    return {"server_url": server_url, "ws_url": ws_url, "issued_at": issued_at, "signature": signature}


async def _prepare(
    session: AsyncSession, p: Principal, agent: Agent, ctype: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Validates against the agent's site and resolves portal ids into what the agent needs."""
    prep = _PREPARERS.get(ctype)
    return await prep(session, p, agent, params) if prep else params


async def _prep_scan(
    session: AsyncSession, _p: Principal, agent: Agent, params: dict[str, Any]
) -> dict[str, Any]:
    if params.get("range_id"):
        rng = await session.get(IpRange, uuid.UUID(params["range_id"]))
        if rng is None or rng.site_id != agent.site_id or rng.status != "approved":
            raise bad_request("range_not_in_site", "Faixa não pertence ao local do coletor (ou não aprovada)")
    return params


async def _prep_read_now(
    session: AsyncSession, _p: Principal, agent: Agent, params: dict[str, Any]
) -> dict[str, Any]:
    ids = params.get("device_ids") or []
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Device).where(
                Device.id.in_([uuid.UUID(i) for i in ids]),
                Device.site_id == agent.site_id,
                Device.deleted_at.is_(None),
            )
        )
    ).scalars()
    devices = [{"id": str(d.id), "serial": d.serial, "ip": d.ip, "port": d.snmp_port} for d in rows]
    if len(devices) != len(set(ids)):
        raise bad_request("devices_not_in_site", "Há equipamentos que não pertencem ao local do coletor")
    return {"devices": devices}


async def _prep_walk(
    session: AsyncSession, _p: Principal, agent: Agent, params: dict[str, Any]
) -> dict[str, Any]:
    # Várias impressoras podem compartilhar o IP (portas SNMP diferentes): casa IP e porta.
    device = (
        await session.execute(
            select(Device.id).where(
                Device.site_id == agent.site_id,
                Device.ip == params["ip"],
                Device.snmp_port == params["port"],
                Device.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    return {**params, "device_id": str(device) if device else None}


async def _prep_set_config(
    _session: AsyncSession, _p: Principal, agent: Agent, _params: dict[str, Any]
) -> dict[str, Any]:
    return {"config_version": agent.config_version}


async def _prep_pause(
    _session: AsyncSession, _p: Principal, agent: Agent, _params: dict[str, Any]
) -> dict[str, Any]:
    agent.paused = True
    agent.state = "paused"
    return {}


async def _prep_resume(
    _session: AsyncSession, _p: Principal, agent: Agent, _params: dict[str, Any]
) -> dict[str, Any]:
    agent.paused = False
    agent.state = "online" if agent.last_seen_at else "offline"
    return {}


async def _prep_promote(
    session: AsyncSession, p: Principal, agent: Agent, _params: dict[str, Any]
) -> dict[str, Any]:
    await _promote(session, p, agent)
    return {}


async def _prep_wake(
    session: AsyncSession, _p: Principal, agent: Agent, params: dict[str, Any]
) -> dict[str, Any]:
    target = await session.get(Agent, uuid.UUID(params["target_agent_id"]))
    if target is None or target.deleted_at is not None or target.site_id != agent.site_id:
        raise bad_request("target_not_in_site", "O PC a ligar precisa ser de outro coletor do mesmo local")
    if target.id == agent.id:
        raise bad_request("target_is_self", "Escolha o coletor de OUTRO PC do local")
    if not target.host_mac:
        raise bad_request("target_without_mac", "O coletor de destino ainda não informou o MAC do PC")
    return {"target_agent_id": str(target.id), "mac": target.host_mac, "target_ips": list(target.local_ips)}


async def _prep_update(
    session: AsyncSession, _p: Principal, agent: Agent, params: dict[str, Any]
) -> dict[str, Any]:
    release = await releases.find_release(session, agent, params["component"], params["version"])
    return releases.update_params(release)


async def _prep_rollback(
    _session: AsyncSession, _p: Principal, agent: Agent, _params: dict[str, Any]
) -> dict[str, Any]:
    previous = (agent.watchdog_status or {}).get("previous_agent_version") or ""
    if not previous:
        raise conflict("no_previous_version", "O vigia não tem versão anterior guardada para voltar")
    return {"component": "agent", "expected_version": previous}


async def _prep_uninstall(
    _session: AsyncSession, p: Principal, agent: Agent, params: dict[str, Any]
) -> dict[str, Any]:
    # Seção 4.7: confirmação dupla no portal e papel admin.
    if p.level < role_level(RESELLER_ADMIN):
        raise forbidden("Só administradores desinstalam o coletor do PC do cliente")
    p.require("agents.delete")
    if params["confirm_name"].strip() != agent.name:
        raise bad_request(
            "confirmation_mismatch", "Digite o nome do coletor exatamente como aparece no portal"
        )
    return {}


_Preparer = Callable[[AsyncSession, Principal, Agent, dict[str, Any]], Awaitable[dict[str, Any]]]


async def _prep_read_device(
    _session: AsyncSession, _p: Principal, _agent: Agent, params: dict[str, Any]
) -> dict[str, Any]:
    """Perfil em teste: validado pelo mesmo schema do agente antes de sair do servidor (seção 6.6)."""
    from app.services.catalog import ProfileError, validate_profile  # noqa: PLC0415 - evita ciclo

    if params.get("profile") is None:
        params.pop("profile", None)
        return params
    try:
        validate_profile(params["profile"])
    except ProfileError as exc:
        raise bad_request("invalid_profile", f"Perfil inválido: {exc}") from exc
    return params


_PREPARERS: dict[str, _Preparer] = {
    "read_device": _prep_read_device,
    "scan_now": _prep_scan,
    "read_now": _prep_read_now,
    "mib_walk": _prep_walk,
    "set_config": _prep_set_config,
    "pause": _prep_pause,
    "resume": _prep_resume,
    "promote_master": _prep_promote,
    "wake_host": _prep_wake,
    "update": _prep_update,
    "rollback": _prep_rollback,
    "uninstall": _prep_uninstall,
}


async def _promote(session: AsyncSession, p: Principal, agent: Agent) -> None:
    """Operator forces this agent as MASTER of its site (PROMPT 4.7/4.8)."""
    site = await session.get(Site, agent.site_id, with_for_update=True)
    if site is None:
        raise not_found("Local")
    old = site.master_agent_id
    site.master_agent_id = agent.id
    site.master_lease_expires_at = _now() + agents_svc.LEASE_DURATION
    agent.cluster_role = "master"
    if old is not None and old != agent.id:
        await session.execute(update(Agent).where(Agent.id == old).values(cluster_role="standby"))
    if old != agent.id:
        session.add(
            ClusterEvent(
                reseller_id=agent.reseller_id,
                site_id=site.id,
                from_agent_id=old,
                to_agent_id=agent.id,
                reason="manual_promote",
                created_by=p.user_id,
            )
        )


async def get_command(session: AsyncSession, p: Principal, command_id: uuid.UUID) -> Command:
    p.require("agents.read")
    cmd = await session.get(Command, command_id)
    if cmd is None or not p.can_access_reseller(cmd.reseller_id):
        raise not_found("Comando")
    await agents_svc.get_agent(session, p, cmd.agent_id)  # aplica o escopo de cliente
    return cmd


COMMAND_SORTS = {"created_at": SortOption(Command.created_at, "datetime")}


async def list_commands(
    session: AsyncSession,
    p: Principal,
    agent_id: uuid.UUID,
    *,
    state: str | None,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> PageResult[Command]:
    agent = await agents_svc.get_agent(session, p, agent_id)
    stmt = select(Command).where(Command.agent_id == agent.id)
    if state:
        stmt = stmt.where(Command.state == state)
    return await paginate(
        session,
        stmt,
        id_column=Command.id,
        sort_options=COMMAND_SORTS,
        sort="created_at",
        direction=direction,
        limit=limit,
        cursor=cursor,
    )


async def cancel_command(session: AsyncSession, p: Principal, command_id: uuid.UUID) -> Command:
    p.require("agents.command")
    cmd = await get_command(session, p, command_id)
    if cmd.state in TERMINAL_STATES:
        raise conflict("command_finished", "O comando já terminou")
    before = cmd.state
    cmd.state = "cancelled"
    cmd.finished_at = _now()
    await audit.record(
        session,
        p,
        action="command.cancel",
        entity="command",
        entity_id=cmd.id,
        reseller_id=cmd.reseller_id,
        before={"state": before},
        after={"state": "cancelled"},
    )
    await notify(session, CH_COMMAND, str(cmd.agent_id))
    await emit_commands(session, [cmd])
    return cmd


# ----------------------------------------------------------------------------- agent side


def to_message(cmd: Command) -> proto.CommandMessage:
    return proto.CommandMessage(
        id=str(cmd.id), type=cmd.type, params=cmd.params, created_at=cmd.created_at, expires_at=cmd.expires_at
    )


async def claim_for_delivery(
    session: AsyncSession, agent_id: uuid.UUID, target: str = "agent"
) -> list[Command]:
    """Commands to hand to the agent (or to its watchdog) now: pending ones, plus delivered-but-
    unconfirmed ones (the connection may have dropped in between). Marks them `sent`."""
    now = _now()
    rows = list(
        (
            await session.execute(
                select(Command)
                .where(
                    Command.agent_id == agent_id,
                    Command.target == target,
                    Command.expires_at > now,
                    or_(
                        Command.state == "pending",
                        (Command.state == "sent") & (Command.sent_at < now - RESEND_UNACKED_AFTER),
                    ),
                )
                .order_by(Command.created_at)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for cmd in rows:
        cmd.state = "sent"
        cmd.sent_at = now
    if rows:
        await emit_commands(session, rows)
    return rows


async def recently_cancelled(session: AsyncSession, agent_id: uuid.UUID) -> list[uuid.UUID]:
    """Cancelled commands the agent may already be running (so the gateway tells it to stop)."""
    since = _now() - timedelta(minutes=2)
    return list(
        (
            await session.execute(
                select(Command.id).where(
                    Command.agent_id == agent_id,
                    Command.state == "cancelled",
                    Command.sent_at.is_not(None),
                    Command.finished_at > since,
                )
            )
        ).scalars()
    )


async def apply_update(session: AsyncSession, agent: Agent, upd: proto.CommandUpdate) -> Command:
    try:
        cid = uuid.UUID(upd.id)
    except ValueError as exc:
        raise not_found("Comando") from exc
    cmd = await session.get(Command, cid, with_for_update=True)
    if cmd is None or cmd.agent_id != agent.id:
        raise not_found("Comando")
    if cmd.state in TERMINAL_STATES:
        if cmd.state != upd.state:
            logger.info("comando %s já estava %s; atualização %s ignorada", cmd.id, cmd.state, upd.state)
        return cmd
    now = _now()
    cmd.sent_at = cmd.sent_at or now
    if upd.progress is not None:
        cmd.progress = upd.progress
    if upd.state in ("acked", "running") and _STEP[upd.state] > _STEP[cmd.state]:
        cmd.acked_at = cmd.acked_at or now
        if upd.state == "running":
            cmd.started_at = cmd.started_at or now
        cmd.state = upd.state
    elif upd.state in ("succeeded", "failed"):
        cmd.acked_at = cmd.acked_at or now
        cmd.started_at = cmd.started_at or now
        cmd.finished_at = now
        cmd.state = upd.state
        result = dict(upd.result or {})
        if upd.error:
            result["error"] = upd.error
        cmd.result = result
        if upd.output is not None:
            raw = upd.output.encode("utf-8")
            cmd.output = (
                raw[:OUTPUT_LIMIT].decode("utf-8", errors="ignore") if len(raw) > OUTPUT_LIMIT else upd.output
            )
        if cmd.type == "set_config" and upd.state == "succeeded":
            applied = result.get("applied_config_version")
            if isinstance(applied, int):
                agent.applied_config_version = applied
    await emit_commands(session, [cmd])
    return cmd


async def expire_commands(session: AsyncSession) -> int:
    """Worker job: undelivered/unstarted commands past `expires_at` → expired; running ones without
    news long after that → failed. Returns how many changed."""
    now = _now()
    expired = list(
        (
            await session.execute(
                update(Command)
                .where(Command.state.in_(("pending", "sent", "acked")), Command.expires_at < now)
                .values(state="expired", finished_at=now)
                .returning(Command)
            )
        ).scalars()
    )
    stuck = list(
        (
            await session.execute(
                update(Command)
                .where(Command.state == "running", Command.expires_at < now - RUNNING_GRACE)
                .values(
                    state="failed",
                    finished_at=now,
                    result={"error": "O coletor não informou o resultado (tempo esgotado)"},
                )
                .returning(Command)
            )
        ).scalars()
    )
    if expired or stuck:
        await emit_commands(session, expired + stuck)
    return len(expired) + len(stuck)


# ----------------------------------------------------------------------------- uploads


async def _upload_command(session: AsyncSession, agent: Agent, command_id: uuid.UUID, ctype: str) -> Command:
    cmd = await session.get(Command, command_id)
    if cmd is None or cmd.agent_id != agent.id or cmd.type != ctype:
        raise not_found("Comando")
    if cmd.state in ("expired", "cancelled"):
        raise conflict("command_finished", "O comando foi cancelado ou expirou")
    return cmd


def _write(settings: Settings, kind: str, reseller_id: uuid.UUID, name: str, data: bytes) -> Path:
    folder = settings.storage_dir / kind / str(reseller_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


async def store_logs(
    session: AsyncSession, settings: Settings, agent: Agent, command_id: uuid.UUID, data: bytes
) -> AgentLog:
    cmd = await _upload_command(session, agent, command_id, "get_logs")
    if len(data) > MAX_LOG_UPLOAD:
        raise bad_request("upload_too_large", "Arquivo de logs grande demais (máx. 50 MB)")
    if not data.startswith(b"PK"):
        raise bad_request("invalid_zip", "Os logs devem vir em um arquivo .zip")
    log_id = uuid.uuid4()
    path = _write(settings, "agent-logs", agent.reseller_id, f"{log_id}.zip", data)
    row = AgentLog(
        id=log_id,
        reseller_id=agent.reseller_id,
        agent_id=agent.id,
        command_id=cmd.id,
        source=cmd.target,
        file_path=str(path),
        size_bytes=len(data),
        hours=int(cmd.params.get("hours", 24)),
    )
    session.add(row)
    await session.flush()
    return row


def count_snmprec_lines(gz: bytes) -> int:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(gz)) as f:
            raw = f.read(MAX_WALK_UNCOMPRESSED + 1)
    except (OSError, EOFError) as exc:
        raise bad_request("invalid_gzip", "Walk deve vir em .snmprec compactado com gzip") from exc
    if len(raw) > MAX_WALK_UNCOMPRESSED:
        raise bad_request("upload_too_large", "Walk grande demais")
    count = 0
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line.strip() and not line.startswith("#"):
            if line.count("|") < 2:  # noqa: PLR2004 - oid|tipo|valor
                raise bad_request("invalid_snmprec", f"Linha inválida no walk: {line[:80]!r}")
            count += 1
    return count


async def store_walk(
    session: AsyncSession, settings: Settings, agent: Agent, command_id: uuid.UUID, data: bytes
) -> MibWalk:
    cmd = await _upload_command(session, agent, command_id, "mib_walk")
    if len(data) > MAX_WALK_UPLOAD:
        raise bad_request("upload_too_large", "Walk grande demais (máx. 64 MB compactado)")
    oids = count_snmprec_lines(data)
    walk_id = uuid.uuid4()
    path = _write(settings, "mib-walks", agent.reseller_id, f"{walk_id}.snmprec.gz", data)
    device_id = cmd.params.get("device_id")
    row = MibWalk(
        id=walk_id,
        reseller_id=agent.reseller_id,
        device_id=uuid.UUID(device_id) if device_id else None,
        agent_id=agent.id,
        command_id=cmd.id,
        ip=str(cmd.params.get("ip", "")),
        port=cmd.params.get("port"),
        root_oid=cmd.params.get("root_oid"),
        file_path=str(path),
        oid_count=oids,
        created_by=cmd.created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def get_agent_log(session: AsyncSession, p: Principal, log_id: uuid.UUID) -> AgentLog:
    p.require("agents.command")
    row = await session.get(AgentLog, log_id)
    if row is None or not p.can_access_reseller(row.reseller_id):
        raise not_found("Arquivo de logs")
    await agents_svc.get_agent(session, p, row.agent_id)
    return row


async def list_agent_logs(session: AsyncSession, p: Principal, agent_id: uuid.UUID) -> list[AgentLog]:
    p.require("agents.command")
    agent = await agents_svc.get_agent(session, p, agent_id)
    return list(
        (
            await session.execute(
                select(AgentLog)
                .where(AgentLog.agent_id == agent.id)
                .order_by(AgentLog.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )


async def get_walk(session: AsyncSession, p: Principal, walk_id: uuid.UUID) -> MibWalk:
    p.require("devices.read")
    row = await session.get(MibWalk, walk_id)
    if row is None or not p.can_access_reseller(row.reseller_id) or row.agent_id is None:
        raise not_found("Walk")
    await agents_svc.get_agent(session, p, row.agent_id)
    return row


async def list_walks(
    session: AsyncSession, p: Principal, *, agent_id: uuid.UUID | None, device_id: uuid.UUID | None
) -> list[MibWalk]:
    p.require("devices.read")
    stmt = select(MibWalk).where(MibWalk.agent_id.is_not(None))
    if agent_id:
        agent = await agents_svc.get_agent(session, p, agent_id)
        stmt = stmt.where(MibWalk.agent_id == agent.id)
    elif not p.is_superadmin:
        stmt = stmt.where(MibWalk.reseller_id == p.reseller_id)
    if device_id:
        stmt = stmt.where(MibWalk.device_id == device_id)
    rows = list((await session.execute(stmt.order_by(MibWalk.created_at.desc()).limit(100))).scalars())
    if p.customer_id is not None:
        allowed = set(
            (
                await session.execute(
                    select(Agent.id)
                    .join(Site, Site.id == Agent.site_id)
                    .where(Site.customer_id == p.customer_id)
                )
            ).scalars()
        )
        rows = [r for r in rows if r.agent_id in allowed]
    return rows
