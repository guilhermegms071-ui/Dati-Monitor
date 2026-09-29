"""Cluster lease and failover (PROMPT 4.8)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Agent, ClusterEvent, Command, Site
from app.services import cluster
from tests.agent_helpers import FakeAgent, enrolled_agent
from tests.conftest import Factory, Tenant, auth, login


async def _set(maker: async_sessionmaker[AsyncSession], agent: FakeAgent, **values: Any) -> None:
    async with maker() as s:
        await s.execute(update(Agent).where(Agent.id == uuid.UUID(agent.agent_id)).values(**values))
        await s.commit()


async def _expire_master(maker: async_sessionmaker[AsyncSession], t: Tenant, master: FakeAgent) -> None:
    """The MASTER's PC went down: no heartbeat for 5 min, lease past its 3 min."""
    past = datetime.now(UTC) - timedelta(minutes=5)
    await _set(maker, master, last_seen_at=past, state="offline")
    async with maker() as s:
        await s.execute(update(Site).where(Site.id == t.site_id).values(master_lease_expires_at=past))
        await s.commit()


async def _failover(maker: async_sessionmaker[AsyncSession]) -> list[cluster.MasterChange]:
    async with maker() as s:
        changes = await cluster.failover(s)
        await s.commit()
    return changes


async def _roles(
    maker: async_sessionmaker[AsyncSession], t: Tenant
) -> tuple[uuid.UUID | None, dict[str, str]]:
    async with maker() as s:
        site = await s.get(Site, t.site_id)
        agents = (await s.execute(select(Agent).where(Agent.site_id == t.site_id))).scalars()
        return (site.master_agent_id if site else None), {a.name: a.cluster_role for a in agents}


async def test_expired_lease_promotes_best_standby_and_old_master_stays_standby(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    a = await enrolled_agent(client, t, name="PC A")
    b = await enrolled_agent(client, t, name="PC B")
    c = await enrolled_agent(client, t, name="PC C")
    assert (await a.heartbeat())["cluster_role"] == "master"  # primeiro a falar assume
    await b.heartbeat(latency_ms=40)
    await c.heartbeat(latency_ms=5)
    await _set(sessionmaker, b, priority=50)  # menor número = preferido; empate cairia na latência
    await _set(sessionmaker, c, priority=100)

    assert await _failover(sessionmaker) == []  # lease em dia: nada muda
    await _expire_master(sessionmaker, t, a)
    changes = await _failover(sessionmaker)
    assert [(ch.to_agent_id, ch.reason) for ch in changes] == [(uuid.UUID(b.agent_id), "lease_expired")]
    master, roles = await _roles(sessionmaker, t)
    assert master == uuid.UUID(b.agent_id)
    assert roles == {"PC A": "standby", "PC B": "master", "PC C": "standby"}
    async with sessionmaker() as s:
        promote = (await s.execute(select(Command).where(Command.type == "promote_master"))).scalar_one()
        assert (str(promote.agent_id), promote.created_by, promote.target) == (b.agent_id, None, "agent")
        event = (
            await s.execute(select(ClusterEvent).where(ClusterEvent.reason == "lease_expired"))
        ).scalar_one()
        assert (str(event.from_agent_id), str(event.to_agent_id)) == (a.agent_id, b.agent_id)

    # O antigo MASTER volta e NÃO retoma sozinho.
    assert (await a.heartbeat())["cluster_role"] == "standby"
    assert (await b.heartbeat())["cluster_role"] == "master"

    # O histórico continua mostrando o nome de quem saiu, mesmo depois de excluído.
    admin = await login(client, t.admin_email)
    assert (await client.delete(f"/api/v1/agents/{a.agent_id}", headers=auth(admin))).status_code == 204
    cluster = (await client.get(f"/api/v1/sites/{t.site_id}/cluster", headers=auth(admin))).json()
    lease = next(e for e in cluster["events"] if e["reason"] == "lease_expired")
    assert (lease["from_name"], lease["to_name"]) == ("PC A", "PC B")


async def test_latency_breaks_priority_ties_and_nobody_online_changes_nothing(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    a = await enrolled_agent(client, t, name="PC A")
    b = await enrolled_agent(client, t, name="PC B")
    c = await enrolled_agent(client, t, name="PC C")
    d = await enrolled_agent(client, t, name="PC D")
    await a.heartbeat()
    await b.heartbeat(latency_ms=80)
    await c.heartbeat(latency_ms=12)
    await d.heartbeat(latency_ms=1)
    await _expire_master(sessionmaker, t, a)
    # B e C com a mesma prioridade: vence a menor latência média. D seria melhor, mas está pausado.
    await _set(sessionmaker, b, avg_latency_ms=80.0)
    await _set(sessionmaker, c, avg_latency_ms=12.0)
    await _set(sessionmaker, d, avg_latency_ms=1.0, paused=True, state="paused")
    assert [ch.to_agent_id for ch in await _failover(sessionmaker)] == [uuid.UUID(c.agent_id)]

    t2 = await factory.tenant("Revenda B")
    x = await enrolled_agent(client, t2, name="PC X")
    y = await enrolled_agent(client, t2, name="PC Y")
    await x.heartbeat()
    await y.heartbeat()
    await _expire_master(sessionmaker, t2, x)
    await _set(sessionmaker, y, last_seen_at=datetime.now(UTC) - timedelta(minutes=10), state="offline")
    assert await _failover(sessionmaker) == []
    master, _ = await _roles(sessionmaker, t2)
    assert master == uuid.UUID(x.agent_id)  # ninguém online: o lease fica vencido até alguém voltar


async def test_preferred_master_takes_over_when_online(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    a = await enrolled_agent(client, t, name="PC A")
    b = await enrolled_agent(client, t, name="PC B")
    await a.heartbeat()
    await b.heartbeat()
    admin = await login(client, t.admin_email)

    other = await factory.tenant("Revenda B")
    stranger = await enrolled_agent(client, other, name="PC de outro local")
    bad = await client.put(
        f"/api/v1/sites/{t.site_id}/preferred-master",
        json={"agent_id": stranger.agent_id},
        headers=auth(admin),
    )
    assert bad.status_code == 400

    await _set(sessionmaker, b, last_seen_at=datetime.now(UTC) - timedelta(minutes=10), state="offline")
    resp = await client.put(
        f"/api/v1/sites/{t.site_id}/preferred-master", json={"agent_id": b.agent_id}, headers=auth(admin)
    )
    assert resp.status_code == 200, resp.text
    assert {m["name"]: m["is_preferred"] for m in resp.json()["members"]} == {"PC A": False, "PC B": True}
    assert await _failover(sessionmaker) == []  # preferido offline e lease em dia: espera

    await b.heartbeat()
    changes = await _failover(sessionmaker)
    assert [(ch.to_agent_id, ch.reason) for ch in changes] == [(uuid.UUID(b.agent_id), "preferred_master")]
    assert (await a.heartbeat())["cluster_role"] == "standby"
    cleared = await client.put(
        f"/api/v1/sites/{t.site_id}/preferred-master", json={"agent_id": None}, headers=auth(admin)
    )
    assert all(not m["is_preferred"] for m in cleared.json()["members"])
