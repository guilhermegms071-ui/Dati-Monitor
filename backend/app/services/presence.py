"""Agent presence (PROMPT section 2: presence in a table, no Redis): which agents hold a live WebSocket
and on which gateway process, plus the offline sweep done by the worker."""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentPresence
from app.models.agents import PRESENCE_STALE_SECONDS
from app.services import agents as agents_svc

# Sem heartbeat por este tempo → offline. Maior que 2 min de WebSocket caído (o agente só passa ao
# heartbeat por HTTPS depois disso) + 30 s de intervalo, e igual à duração do lease do MASTER.
OFFLINE_AFTER = timedelta(minutes=3)


def _now() -> datetime:
    return datetime.now(UTC)


async def connect(session: AsyncSession, agent: Agent, gateway_id: str, remote_addr: str | None) -> None:
    now = _now()
    stmt = insert(AgentPresence).values(
        agent_id=agent.id,
        reseller_id=agent.reseller_id,
        gateway_id=gateway_id,
        connected_at=now,
        last_seen_at=now,
        remote_addr=remote_addr,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[AgentPresence.agent_id],
            set_={
                "gateway_id": gateway_id,
                "connected_at": now,
                "last_seen_at": now,
                "remote_addr": remote_addr,
                "latency_ms": None,
            },
        )
    )


async def touch(
    session: AsyncSession, agent_id: uuid.UUID, gateway_id: str, latency_ms: float | None = None
) -> None:
    values: dict[str, object] = {"last_seen_at": _now()}
    if latency_ms is not None:
        values["latency_ms"] = latency_ms
    await session.execute(
        update(AgentPresence)
        .where(AgentPresence.agent_id == agent_id, AgentPresence.gateway_id == gateway_id)
        .values(**values)
    )


async def disconnect(session: AsyncSession, agent_id: uuid.UUID, gateway_id: str) -> None:
    # Só apaga se a linha ainda for desta conexão (o agente pode ter reconectado em outro gateway).
    await session.execute(
        delete(AgentPresence).where(
            AgentPresence.agent_id == agent_id, AgentPresence.gateway_id == gateway_id
        )
    )


async def connected_ids(session: AsyncSession, agent_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    ids = list(agent_ids)
    if not ids:
        return set()
    fresh = _now() - timedelta(seconds=PRESENCE_STALE_SECONDS)
    rows = await session.execute(
        select(AgentPresence.agent_id).where(
            AgentPresence.agent_id.in_(ids), AgentPresence.last_seen_at > fresh
        )
    )
    return set(rows.scalars())


async def sweep(session: AsyncSession) -> tuple[int, int]:
    """Worker job: removes presence rows of gateways that died without cleaning up and marks agents
    without any heartbeat for OFFLINE_AFTER as offline. Returns (stale rows, agents now offline)."""
    now = _now()
    stale = await session.execute(
        delete(AgentPresence)
        .where(AgentPresence.last_seen_at < now - timedelta(seconds=PRESENCE_STALE_SECONDS))
        .returning(AgentPresence.agent_id)
    )
    offline = await session.execute(
        update(Agent)
        .where(
            Agent.deleted_at.is_(None),
            Agent.state != "offline",
            Agent.last_seen_at < now - OFFLINE_AFTER,
        )
        .values(state="offline")
        .returning(Agent)
    )
    gone = list(offline.scalars())
    for agent in gone:
        await agents_svc.emit_state(session, agent)
    return len(stale.all()), len(gone)
