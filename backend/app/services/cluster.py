"""Cluster failover (PROMPT 4.8): the server owns the MASTER lease of each site.

The MASTER renews its lease on every heartbeat (`agents._ensure_master`). This worker job:
- promotes the operator's preferred MASTER as soon as it is online (the operator "pins" it);
- when the lease expires (3 min without heartbeat), promotes the online STANDBY with the best priority
  (lowest number; tie: lowest average latency) and sends it `promote_master`.
The old MASTER, when it comes back, is told it is STANDBY on its next heartbeat (it never takes over by
itself). Every change is recorded in `cluster_events` and pushed to the portal.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, ClusterEvent, Site
from app.services import agents as agents_svc
from app.services import commands as commands_svc

logger = logging.getLogger(__name__)

# Coletor "vivo" para assumir: heartbeat recente (mesmo prazo do lease).
ALIVE_WITHIN = agents_svc.LEASE_DURATION
PROMOTE_EXPIRY = timedelta(minutes=10)


@dataclass(frozen=True)
class MasterChange:
    site_id: uuid.UUID
    from_agent_id: uuid.UUID | None
    to_agent_id: uuid.UUID
    reason: str


def _eligible(agent: Agent, now: datetime) -> bool:
    return (
        agent.enrolled_at is not None
        and agent.revoked_at is None
        and agent.deleted_at is None
        and not agent.paused
        and agent.state in ("online", "degraded")
        and agent.last_seen_at is not None
        and now - agent.last_seen_at < ALIVE_WITHIN
    )


def _rank(agent: Agent) -> tuple[int, float]:
    return agent.priority, agent.avg_latency_ms if agent.avg_latency_ms is not None else float("inf")


async def failover(session: AsyncSession, now: datetime | None = None) -> list[MasterChange]:
    now = now or datetime.now(UTC)
    sites = list(
        (
            await session.execute(
                select(Site)
                .where(
                    Site.deleted_at.is_(None),
                    or_(
                        and_(Site.master_agent_id.is_not(None), Site.master_lease_expires_at < now),
                        and_(
                            Site.preferred_master_agent_id.is_not(None),
                            or_(
                                Site.master_agent_id.is_(None),
                                Site.preferred_master_agent_id != Site.master_agent_id,
                            ),
                        ),
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    changes: list[MasterChange] = []
    for site in sites:
        agents = list((await session.execute(select(Agent).where(Agent.site_id == site.id))).scalars())
        by_id = {a.id: a for a in agents}
        candidates = sorted((a for a in agents if _eligible(a, now)), key=_rank)
        preferred = by_id.get(site.preferred_master_agent_id) if site.preferred_master_agent_id else None
        lease_expired = site.master_lease_expires_at is not None and site.master_lease_expires_at < now
        if preferred is not None and preferred.id != site.master_agent_id and preferred in candidates:
            new, reason = preferred, "preferred_master"
        elif lease_expired:
            others = [a for a in candidates if a.id != site.master_agent_id]
            if not others:
                continue  # ninguém online para assumir; o lease fica vencido até alguém voltar
            new, reason = others[0], "lease_expired"
        else:
            continue
        old_id = site.master_agent_id
        site.master_agent_id = new.id
        site.master_lease_expires_at = now + agents_svc.LEASE_DURATION
        new.cluster_role = "master"
        if old_id is not None and old_id != new.id:
            await session.execute(update(Agent).where(Agent.id == old_id).values(cluster_role="standby"))
        session.add(
            ClusterEvent(
                reseller_id=site.reseller_id,
                site_id=site.id,
                from_agent_id=old_id,
                to_agent_id=new.id,
                reason=reason,
                details={"priority": new.priority, "avg_latency_ms": new.avg_latency_ms},
            )
        )
        await commands_svc.insert_command(
            session, None, new, "promote_master", {}, target="agent", expires_in=PROMOTE_EXPIRY, reason=reason
        )
        await agents_svc.emit_state(session, new)
        old = by_id.get(old_id) if old_id else None
        if old is not None and old.id != new.id:
            old.cluster_role = "standby"
            await agents_svc.emit_state(session, old)
        logger.warning("local %s: MASTER passou de %s para %s (%s)", site.id, old_id, new.id, reason)
        changes.append(MasterChange(site_id=site.id, from_agent_id=old_id, to_agent_id=new.id, reason=reason))
    return changes
