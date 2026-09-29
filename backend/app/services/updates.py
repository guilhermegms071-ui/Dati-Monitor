"""Automatic updates (PROMPT 5.2): the worker offers each collector the newest release of its channel.

Rules:
- canary collectors take canary and stable releases; stable collectors only stable ones;
- `rollout_percent`: a stable hash of (agent, release) decides who is in the gradual rollout;
- never offer a release whose failure rate on canary collectors is above the limit (5% by default);
- never retry a release that already failed on that collector (the watchdog rolled it back);
- one update at a time per collector and component, and only when the executor is alive: the watchdog
  updates the agent; the agent updates the watchdog.
"""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Agent, AgentRelease, Command
from app.services import commands as commands_svc
from app.services import releases
from app.services.watchdog import watchdog_alive

logger = logging.getLogger(__name__)

UPDATE_EXPIRY = timedelta(minutes=30)
AGENT_ALIVE_WITHIN = timedelta(minutes=3)
OPEN_STATES = ("pending", "sent", "acked", "running")


def in_rollout(agent_id: uuid.UUID, release_id: uuid.UUID, percent: int) -> bool:
    bucket = int(hashlib.sha256(f"{agent_id}:{release_id}".encode()).hexdigest()[:8], 16) % 100
    return bucket < percent


def _executor_alive(agent: Agent, component: str, now: datetime) -> bool:
    if component == "agent":
        return watchdog_alive(agent, now)
    return agent.last_seen_at is not None and now - agent.last_seen_at < AGENT_ALIVE_WITHIN


async def offer_updates(session: AsyncSession, settings: Settings, now: datetime | None = None) -> int:
    """Creates `update` commands for collectors behind the newest allowed release. Returns how many."""
    if not settings.auto_update:
        return 0
    now = now or datetime.now(UTC)
    available = [
        r
        for r in (await session.execute(select(AgentRelease).where(AgentRelease.yanked.is_(False)))).scalars()
        if r.rollout_percent > 0
    ]
    if not available:
        return 0
    stats = await releases.release_stats(session, [r.id for r in available])
    healthy = [
        r
        for r in available
        if stats[r.id].canary_failure_percent <= settings.update_max_canary_failure_percent
    ]
    for r in available:
        if r not in healthy:
            logger.warning(
                "versão %s (%s %s/%s) fora da atualização automática: %.1f%% de falha no canary",
                r.version,
                r.component,
                r.os,
                r.arch,
                stats[r.id].canary_failure_percent,
            )
    agents = list(
        (
            await session.execute(
                select(Agent).where(
                    Agent.deleted_at.is_(None),
                    Agent.revoked_at.is_(None),
                    Agent.enrolled_at.is_not(None),
                    Agent.arch.is_not(None),
                )
            )
        ).scalars()
    )
    open_or_failed = {
        (agent_id, params.get("component", "agent"), state, params.get("release_id"))
        for agent_id, params, state in (
            await session.execute(
                select(Command.agent_id, Command.params, Command.state).where(
                    Command.type == "update",
                    Command.state.in_((*OPEN_STATES, "failed")),
                )
            )
        ).tuples()
    }
    busy = {(a, c) for a, c, state, _ in open_or_failed if state in OPEN_STATES}
    failed = {(a, rid) for a, _, state, rid in open_or_failed if state == "failed"}
    created = 0
    for agent in agents:
        target = releases.agent_target(agent)
        if target is None:
            continue
        channels = releases.channels_for(agent)
        for component in releases.COMPONENTS:
            current = agent.version if component == "agent" else agent.watchdog_version
            if not current or (agent.id, component) in busy or not _executor_alive(agent, component, now):
                continue
            options = [
                r
                for r in healthy
                if r.component == component
                and (r.os, r.arch) == target
                and r.channel in channels
                and releases.is_newer(r.version, current)
                and (agent.id, str(r.id)) not in failed
                and in_rollout(agent.id, r.id, r.rollout_percent)
            ]
            if not options:
                continue
            best = max(options, key=lambda r: releases.version_key(r.version) or ())
            await commands_svc.insert_command(
                session,
                None,
                agent,
                "update",
                releases.update_params(best),
                target="watchdog" if component == "agent" else "agent",
                expires_in=UPDATE_EXPIRY,
                reason=f"atualização automática {current} → {best.version} (canal {agent.update_channel})",
            )
            created += 1
    return created
