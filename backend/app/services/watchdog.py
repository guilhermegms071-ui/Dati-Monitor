"""dm-watchdog channel (PROMPT 5.1): a plain HTTPS poll every 60 s, independent from the WebSocket and
from the agent's code, so the server can restart/update/roll back an agent that is stuck.

The watchdog authenticates with the agent's token (same machine credential). Its heartbeat records the
agent service state as the watchdog sees it, the restarts it did (with the reason) and the version kept
for rollback, and returns the commands addressed to it.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent
from app.schemas import agent as proto
from app.services import agents as agents_svc
from app.services import commands as commands_svc

logger = logging.getLogger(__name__)

# O watchdog fala a cada 60 s: sem notícias por 3 min = vigia fora do ar.
WATCHDOG_ALIVE_WITHIN = timedelta(minutes=3)
RESTART_HISTORY = timedelta(hours=24)
MAX_RESTARTS_KEPT = 20


def watchdog_alive(agent: Agent, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return (
        agent.last_watchdog_seen_at is not None and now - agent.last_watchdog_seen_at < WATCHDOG_ALIVE_WITHIN
    )


def _merge_restarts(
    previous: list[Any], new: list[proto.WatchdogRestart], now: datetime
) -> list[dict[str, str]]:
    kept = [
        r
        for r in previous
        if isinstance(r, dict)
        and isinstance(r.get("at"), str)
        and datetime.fromisoformat(r["at"]) > now - RESTART_HISTORY
    ]
    kept += [{"at": r.at.astimezone(UTC).isoformat(), "reason": r.reason} for r in new]
    kept.sort(key=lambda r: r["at"])
    return kept[-MAX_RESTARTS_KEPT:]


async def heartbeat(
    session: AsyncSession, agent: Agent, req: proto.WatchdogHeartbeatRequest
) -> proto.WatchdogHeartbeatResponse:
    now = datetime.now(UTC)
    was_alive = watchdog_alive(agent, now)
    status = dict(agent.watchdog_status or {})
    status.update(
        {
            "agent_state": req.agent_state,
            "agent_healthy": req.agent_healthy,
            "agent_version": req.agent_version,
            "agent_memory_bytes": req.agent_memory_bytes,
            "previous_agent_version": req.previous_agent_version,
            "os": req.os,
            "arch": req.arch,
            "errors": req.errors,
            "restarts": _merge_restarts(status.get("restarts", []), req.restarts, now),
        }
    )
    for r in req.restarts:
        logger.warning("watchdog reiniciou o coletor %s: %s", agent.id, r.reason)
    agent.watchdog_status = status
    agent.last_watchdog_seen_at = now
    if req.version:
        agent.watchdog_version = req.version
    if req.arch and not agent.arch:
        agent.arch = req.arch
    cmds = await commands_svc.claim_for_delivery(session, agent.id, target="watchdog")
    if not was_alive or req.restarts:
        await agents_svc.emit_state(session, agent)
    return proto.WatchdogHeartbeatResponse(
        server_time=now, commands=[commands_svc.to_message(c) for c in cmds]
    )
