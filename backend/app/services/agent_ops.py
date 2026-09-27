"""Collector screens (PROMPT 10.5): heartbeat series, the site's cluster, version history, the composite
"Reativar" action (PROMPT 4.7) and bulk commands."""

import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, bad_request
from app.core.principal import Principal
from app.models import Agent, AgentHeartbeat, ClusterEvent
from app.schemas.commands import CommandIn
from app.services import agents as agents_svc
from app.services import audit
from app.services import commands as commands_svc
from app.services import presence as presence_svc

DISPLAY_TZ = ZoneInfo("America/Sao_Paulo")
# Coletor "vivo" para o Reativar: heartbeat recente ou conexão WebSocket de pé.
ALIVE_WITHIN = timedelta(seconds=90)


class HeartbeatPoint(BaseModel):
    ts: datetime
    channel: str
    cpu_percent: float | None
    memory_bytes: int | None
    queue_pending: int | None
    latency_ms: float | None


class VersionSeen(BaseModel):
    version: str
    first_seen: datetime
    last_seen: datetime


class ClusterMember(BaseModel):
    id: uuid.UUID
    name: str
    cluster_role: str
    state: str
    priority: int
    ws_connected: bool
    last_seen_at: datetime | None
    avg_latency_ms: float | None
    is_preferred: bool


class ClusterEventOut(BaseModel):
    created_at: datetime
    reason: str
    from_agent_id: uuid.UUID | None
    to_agent_id: uuid.UUID | None


class SiteCluster(BaseModel):
    site_id: uuid.UUID
    master_agent_id: uuid.UUID | None
    master_lease_expires_at: datetime | None
    members: list[ClusterMember]
    events: list[ClusterEventOut]


class ReactivationStep(BaseModel):
    action: str
    message: str
    agent_id: uuid.UUID | None = None
    command_id: uuid.UUID | None = None


class Reactivation(BaseModel):
    # O servidor sempre envia os campos com default: no OpenAPI de resposta eles são obrigatórios.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    agent_id: uuid.UUID
    outcome: str = Field(description="commands_sent | failover | nothing_online")
    message: str
    steps: list[ReactivationStep]
    suggestions: list[str] = Field(default_factory=list)


class BulkCommandIn(BaseModel):
    agent_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    command: CommandIn


class BulkCommandResult(BaseModel):
    agent_id: uuid.UUID
    command_id: uuid.UUID | None = None
    error: str | None = None


async def heartbeats(
    session: AsyncSession, p: Principal, agent_id: uuid.UUID, hours: int
) -> list[HeartbeatPoint]:
    agent = await agents_svc.get_agent(session, p, agent_id)
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = await session.execute(
        select(AgentHeartbeat)
        .where(AgentHeartbeat.agent_id == agent.id, AgentHeartbeat.ts >= since)
        .order_by(AgentHeartbeat.ts)
        .limit(5000)
    )
    return [
        HeartbeatPoint(
            ts=h.ts,
            channel=h.channel,
            cpu_percent=h.cpu_percent,
            memory_bytes=h.memory_bytes,
            queue_pending=h.queue_pending,
            latency_ms=h.latency_ms,
        )
        for h in rows.scalars()
    ]


async def versions(session: AsyncSession, p: Principal, agent_id: uuid.UUID) -> list[VersionSeen]:
    agent = await agents_svc.get_agent(session, p, agent_id)
    rows = await session.execute(
        select(AgentHeartbeat.version, func.min(AgentHeartbeat.ts), func.max(AgentHeartbeat.ts))
        .where(AgentHeartbeat.agent_id == agent.id, AgentHeartbeat.version.is_not(None))
        .group_by(AgentHeartbeat.version)
        .order_by(func.min(AgentHeartbeat.ts).desc())
    )
    return [VersionSeen(version=v, first_seen=a, last_seen=b) for v, a, b in rows.tuples() if v]


async def cluster(session: AsyncSession, p: Principal, site_id: uuid.UUID) -> SiteCluster:
    p.require("agents.read")
    site = await agents_svc.site_in_scope(session, p, site_id)
    members = list(
        (
            await session.execute(
                select(Agent)
                .where(Agent.site_id == site.id, Agent.deleted_at.is_(None), Agent.revoked_at.is_(None))
                .order_by(Agent.priority, Agent.name)
            )
        ).scalars()
    )
    connected = await presence_svc.connected_ids(session, (a.id for a in members))
    events = (
        await session.execute(
            select(ClusterEvent)
            .where(ClusterEvent.site_id == site.id)
            .order_by(ClusterEvent.created_at.desc())
            .limit(30)
        )
    ).scalars()
    return SiteCluster(
        site_id=site.id,
        master_agent_id=site.master_agent_id,
        master_lease_expires_at=site.master_lease_expires_at,
        members=[
            ClusterMember(
                id=a.id,
                name=a.name,
                cluster_role=a.cluster_role,
                state=a.state,
                priority=a.priority,
                ws_connected=a.id in connected,
                last_seen_at=a.last_seen_at,
                avg_latency_ms=a.avg_latency_ms,
                is_preferred=site.preferred_master_agent_id == a.id,
            )
            for a in members
        ],
        events=[
            ClusterEventOut(
                created_at=e.created_at,
                reason=e.reason,
                from_agent_id=e.from_agent_id,
                to_agent_id=e.to_agent_id,
            )
            for e in events
        ],
    )


def _alive(agent: Agent, connected: set[uuid.UUID], now: datetime) -> bool:
    return agent.id in connected or (
        agent.last_seen_at is not None and now - agent.last_seen_at < ALIVE_WITHIN
    )


async def reactivate(
    session: AsyncSession, settings: Settings, p: Principal, agent_id: uuid.UUID
) -> Reactivation:
    """The most important button of the system (PROMPT 4.7):
    1. agent connected → `reconnect` + `read_now`;
    2. agent offline but its watchdog alive → `restart_agent` to the watchdog (arrives with the
       watchdog, Phase 5);
    3. both offline and another collector of the site online → `promote_master` on it + `wake_host`
       to the fallen PC;
    4. nothing answers → clear diagnosis and manual suggestions."""
    p.require("agents.command")
    agent = await agents_svc.get_agent(session, p, agent_id)
    if agent.revoked_at is not None:
        raise bad_request("agent_revoked", "Coletor revogado: cadastre-o de novo em vez de reativar")
    now = datetime.now(UTC)
    siblings = list(
        (
            await session.execute(
                select(Agent).where(
                    Agent.site_id == agent.site_id,
                    Agent.id != agent.id,
                    Agent.deleted_at.is_(None),
                    Agent.revoked_at.is_(None),
                    Agent.enrolled_at.is_not(None),
                )
            )
        ).scalars()
    )
    connected = await presence_svc.connected_ids(session, [agent.id, *(s.id for s in siblings)])
    steps: list[ReactivationStep] = []

    async def send(target: Agent, ctype: str, params: dict[str, object] | None = None) -> uuid.UUID:
        cmd = await commands_svc.create_command(
            session,
            settings,
            p,
            target.id,
            CommandIn.model_validate({"type": ctype, "params": dict(params or {})}),
        )
        return cmd.id

    if _alive(agent, connected, now):
        for ctype, msg in (
            ("reconnect", "Reconectando o canal e reenviando a fila"),
            ("read_now", "Lendo todos os equipamentos agora"),
        ):
            steps.append(
                ReactivationStep(
                    action=ctype, message=msg, agent_id=agent.id, command_id=await send(agent, ctype)
                )
            )
        result = Reactivation(
            agent_id=agent.id,
            outcome="commands_sent",
            message="Coletor está conectado: comandos enviados",
            steps=steps,
        )
    else:
        online = sorted(
            (s for s in siblings if _alive(s, connected, now)),
            key=lambda s: (s.id not in connected, s.priority, s.avg_latency_ms or 1e9),
        )
        if online:
            helper = online[0]
            steps.append(
                ReactivationStep(
                    action="promote_master",
                    message=f"Coletor “{helper.name}” assume como MASTER do local",
                    agent_id=helper.id,
                    command_id=await send(helper, "promote_master"),
                )
            )
            if agent.host_mac:
                steps.append(
                    ReactivationStep(
                        action="wake_host",
                        message=f"“{helper.name}” envia Wake-on-LAN para o PC de “{agent.name}”",
                        agent_id=helper.id,
                        command_id=await send(helper, "wake_host", {"target_agent_id": str(agent.id)}),
                    )
                )
            else:
                steps.append(
                    ReactivationStep(
                        action="wake_host",
                        message="Wake-on-LAN não enviado: o coletor caído nunca informou o MAC do PC",
                    )
                )
            result = Reactivation(
                agent_id=agent.id,
                outcome="failover",
                message=f"“{agent.name}” está offline; a coleta passa para “{helper.name}”",
                steps=steps,
            )
        else:
            last = (
                agent.last_seen_at.astimezone(DISPLAY_TZ).strftime("%d/%m %H:%M")
                if agent.last_seen_at
                else "nunca"
            )
            result = Reactivation(
                agent_id=agent.id,
                outcome="nothing_online",
                message=(
                    f"Nenhum coletor deste local está ligado. Último sinal: {last}. "
                    "Provável PC desligado ou sem internet."
                ),
                steps=[],
                suggestions=[
                    "Peça a alguém no cliente para ligar o PC do coletor "
                    "(ou verificar se está conectado à rede).",
                    "Confirme se a internet do cliente está funcionando.",
                    "Se o PC foi trocado, cadastre um novo coletor para o local (botão Novo coletor).",
                ],
            )
    await audit.record(
        session,
        p,
        action="agent.reactivate",
        entity="agent",
        entity_id=agent.id,
        reseller_id=agent.reseller_id,
        after={"outcome": result.outcome, "steps": [s.model_dump(mode="json") for s in steps]},
    )
    return result


async def bulk_commands(
    session: AsyncSession, settings: Settings, p: Principal, data: BulkCommandIn
) -> list[BulkCommandResult]:
    out: list[BulkCommandResult] = []
    for agent_id in dict.fromkeys(data.agent_ids):
        try:
            async with session.begin_nested():
                cmd = await commands_svc.create_command(session, settings, p, agent_id, data.command)
            out.append(BulkCommandResult(agent_id=agent_id, command_id=cmd.id))
        except AppError as exc:
            out.append(BulkCommandResult(agent_id=agent_id, error=exc.message))
    return out


async def log_tail(session: AsyncSession, p: Principal, log_id: uuid.UUID, lines: int) -> str:
    """Last lines of the newest log file inside an uploaded zip (the portal's log viewer)."""
    row = await commands_svc.get_agent_log(session, p, log_id)
    try:
        with zipfile.ZipFile(row.file_path) as zf:
            infos = sorted(zf.infolist(), key=lambda i: i.date_time, reverse=True)
            if not infos:
                return ""
            raw = zf.read(infos[0])
    except (OSError, zipfile.BadZipFile) as exc:
        raise AppError(500, "log_unreadable", f"Não foi possível ler o arquivo de logs: {exc}") from exc
    text = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="replace").read()
    return "\n".join(text.splitlines()[-lines:])
