"""Collector protocol: portal creates agent + code, enrollment, HMAC tokens, heartbeat/cluster, config."""

import base64
import time
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Agent, AgentHeartbeat, ClusterEvent, IpRange, Site
from tests.agent_helpers import FakeAgent, create_agent, enrolled_agent
from tests.conftest import Factory, auth, login

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_create_agent_returns_code_and_instructions(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    created = await create_agent(client, admin, t.site_id)
    code = created["enrollment"]["code"]
    assert len(code) == 8
    assert not set(code) & set("01IO")
    assert f"--code {code}" in created["enrollment"]["install_command"]
    assert len(created["enrollment"]["instructions"]) == 3
    assert created["agent"]["state"] == "offline"
    assert created["agent"]["enrolled_at"] is None
    # Técnico não cria coletor; cliente não vê de outra revenda.
    _, tech = await factory.user(t.reseller_id, role="technician")
    resp = await client.post(
        "/api/v1/agents",
        json={"site_id": str(t.site_id), "name": "X X"},
        headers=auth(await login(client, tech)),
    )
    assert resp.status_code == 403


async def test_enrollment_is_single_use_and_secret_not_stored(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    created = await create_agent(client, admin, t.site_id)
    code = created["enrollment"]["code"]
    body = {
        "v": 1,
        "code": code,
        "hostname": "PC-01",
        "os": "Windows 10",
        "arch": "amd64",
        "kind": "windows",
        "version": "1.0.0",
        "local_ips": ["192.168.0.10"],
    }
    first = await client.post("/api/agent/enroll", json=body)
    assert first.status_code == 200, first.text
    secret = base64.b64decode(first.json()["secret"])
    assert len(secret) == 32
    again = await client.post("/api/agent/enroll", json=body)
    assert again.status_code == 401
    assert again.json()["detail"]["code"] == "enrollment_code_invalid"
    async with sessionmaker() as s:
        agent = await s.get(Agent, created["agent"]["id"])
        assert agent is not None
        assert agent.hostname == "PC-01"
        assert agent.secret_hash is not None
        assert base64.b64encode(secret).decode() not in agent.secret_hash
        assert secret.hex() not in agent.secret_hash
    bad = await client.post("/api/agent/enroll", json={**body, "code": "ZZZZZZZZ"})
    assert bad.status_code == 401
    malformed = await client.post("/api/agent/enroll", json={**body, "code": "abc"})
    assert malformed.status_code == 422


async def test_token_signature_skew_replay_and_revocation(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    wrong = await client.post("/api/agent/token", json=agent.token_request(key=b"x" * 32))
    assert wrong.json()["detail"]["code"] == "signature_invalid"
    skewed = await client.post("/api/agent/token", json=agent.token_request(ts=int(time.time()) - 3600))
    assert skewed.status_code == 401
    assert skewed.json()["detail"]["code"] == "clock_skew"
    assert "server_time" in skewed.json()["detail"]
    req = agent.token_request()
    assert (await client.post("/api/agent/token", json=req)).status_code == 200
    replay = await client.post("/api/agent/token", json=req)
    assert replay.json()["detail"]["code"] == "nonce_reused"
    unknown = await client.post(
        "/api/agent/token", json={**agent.token_request(), "agent_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert unknown.status_code == 401

    # Portal token não serve para a API do agente e vice-versa.
    admin = await login(client, t.admin_email)
    assert (await client.get("/api/agent/config", headers=auth(admin))).status_code == 401
    assert (await client.get("/api/v1/customers", headers=agent.headers)).status_code == 401

    revoked = await client.post(f"/api/v1/agents/{agent.agent_id}/revoke", headers=auth(admin))
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    hb = await client.post(
        "/api/agent/heartbeat", json={"v": 1, "ts": "2026-01-01T00:00:00Z"}, headers=agent.headers
    )
    assert hb.json()["detail"]["code"] == "agent_revoked"
    tok = await client.post("/api/agent/token", json=agent.token_request())
    assert tok.json()["detail"]["code"] == "agent_revoked"
    assert (
        await client.post(f"/api/v1/agents/{agent.agent_id}/revoke", headers=auth(admin))
    ).status_code == 409

    # Novo código recadastra o mesmo coletor.
    code = (
        await client.post(f"/api/v1/agents/{agent.agent_id}/enrollment-code", headers=auth(admin))
    ).json()["code"]
    re_enroll = await client.post("/api/agent/enroll", json={"v": 1, "code": code, "kind": "windows"})
    assert re_enroll.status_code == 200
    assert re_enroll.json()["agent_id"] == agent.agent_id


async def test_heartbeat_first_agent_is_master_second_is_standby(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    a1 = await enrolled_agent(client, t, "PC 1")
    a2 = await enrolled_agent(client, t, "PC 2")
    r1 = await a1.heartbeat(
        queue_pending=3,
        cpu_percent=1.5,
        memory_bytes=30_000_000,
        uptime_seconds=60,
        local_ips=["192.168.10.20"],
        applied_config_version=0,
    )
    r2 = await a2.heartbeat()
    assert r1["cluster_role"] == "master"
    assert r2["cluster_role"] == "standby"
    assert r1["config_version"] >= 1  # criação do local/perfis já incrementou
    degraded = await a2.heartbeat(queue_pending=5000)
    assert degraded["cluster_role"] == "standby"
    async with sessionmaker() as s:
        agents = {str(a.id): a for a in (await s.execute(select(Agent))).scalars()}
        assert agents[a1.agent_id].state == "online"
        assert agents[a1.agent_id].queue_pending == 3
        assert agents[a2.agent_id].state == "degraded"
        site = await s.get(Site, t.site_id)
        assert site is not None
        assert str(site.master_agent_id) == a1.agent_id
        assert site.master_lease_expires_at is not None
        beats = (await s.execute(select(AgentHeartbeat))).scalars().all()
        assert len(beats) == 3
        events = (await s.execute(select(ClusterEvent))).scalars().all()
        assert [e.reason for e in events] == ["first_agent"]


async def test_config_contains_ranges_credentials_and_profiles(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    h = auth(admin)
    # Local criado pela API ganha a credencial padrão "public".
    site = (
        await client.post(
            "/api/v1/sites",
            json={
                "customer_id": str(t.customer_id),
                "name": "Novo Local",
                "collection_config": {"counters_minutes": 30},
            },
            headers=h,
        )
    ).json()
    creds = (await client.get(f"/api/v1/sites/{site['id']}/snmp-credentials", headers=h)).json()
    assert [(c["version"], c["community_hint"]) for c in creds] == [("v2c", "p…c")]
    await client.post(
        f"/api/v1/sites/{site['id']}/ip-ranges",
        json={"cidr": "127.0.0.1/32", "ports": [1161, 1165]},
        headers=h,
    )
    created = await create_agent(client, admin, site["id"])
    enroll = await client.post(
        "/api/agent/enroll", json={"v": 1, "code": created["enrollment"]["code"], "kind": "linux"}
    )
    agent = FakeAgent(client, enroll.json()["agent_id"], base64.b64decode(enroll.json()["secret"]))
    await agent.authenticate()
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert cfg["intervals"]["counters_minutes"] == 30
    assert cfg["intervals"]["discovery_minutes"] == 360
    assert cfg["discovery"] == {
        "concurrency": 64,
        "rate_pps": 200,
        "timeout_ms": 1500,
        "retries": 1,
        "read_timeout_ms": 2000,
    }
    assert (cfg["monitor_local_networks"], cfg["ignored_serials"]) == (False, [])
    assert cfg["ranges"][0]["cidr"] == "127.0.0.1/32"
    assert cfg["ranges"][0]["ports"] == [1161, 1165]
    assert cfg["credentials"][0]["community"] == "public"
    ids = {p["id"] for p in cfg["profiles"]}
    assert {"canon", "konica-minolta", "generic"} <= ids
    konica = next(p for p in cfg["profiles"] if p["id"] == "konica-minolta")
    assert konica["counter_sources"][0]["counters"]["mono"]["expr"] == "copy_mono + print_mono"

    before = cfg["config_version"]
    await client.post(
        f"/api/v1/sites/{site['id']}/snmp-credentials",
        json={"version": "v2c", "community": "segredo"},
        headers=h,
    )
    hb = await agent.heartbeat()
    assert hb["config_version"] == before + 1
    cfg2 = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert [c["community"] for c in cfg2["credentials"]] == ["public", "segredo"]


async def test_range_suggestion_needs_approval(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    resp = await client.post(
        "/api/agent/ranges/suggest",
        json={"v": 1, "ranges": ["192.168.10.0/24", "8.8.8.0/24", "lixo"]},
        headers=agent.headers,
    )
    assert resp.status_code == 200
    none_valid = await client.post(
        "/api/agent/ranges/suggest", json={"v": 1, "ranges": ["8.8.8.0/24"]}, headers=agent.headers
    )
    assert none_valid.status_code == 400
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert cfg["ranges"] == []  # sugestão não é varrida antes da aprovação
    admin = auth(await login(client, t.admin_email))
    ranges = (await client.get(f"/api/v1/sites/{t.site_id}/ip-ranges", headers=admin)).json()
    assert [(r["cidr"], r["status"], r["active"]) for r in ranges] == [
        ("192.168.10.0/24", "suggested", False)
    ]
    approved = await client.post(f"/api/v1/ip-ranges/{ranges[0]['id']}/approve", headers=admin)
    assert approved.json()["status"] == "approved"
    assert (
        await client.post(f"/api/v1/ip-ranges/{ranges[0]['id']}/approve", headers=admin)
    ).status_code == 400
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert [r["cidr"] for r in cfg["ranges"]] == ["192.168.10.0/24"]
    async with sessionmaker() as s:
        a = await s.get(Agent, agent.agent_id)
        assert a is not None
        assert a.suggested_ranges == ["192.168.10.0/24"]
        assert len((await s.execute(select(IpRange))).scalars().all()) == 1


async def test_agent_management_endpoints(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    admin = auth(await login(client, t.admin_email))
    agent = await enrolled_agent(client, t)
    await agent.heartbeat()
    lst = (
        await client.get(
            "/api/v1/agents", params={"site_id": str(t.site_id), "state": "online"}, headers=admin
        )
    ).json()
    assert [a["id"] for a in lst["items"]] == [agent.agent_id]
    assert lst["items"][0]["cluster_role"] == "master"
    upd = await client.patch(
        f"/api/v1/agents/{agent.agent_id}", json={"name": "PC Recepção", "priority": 10}, headers=admin
    )
    assert upd.json()["name"] == "PC Recepção"
    one = (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=admin)).json()
    assert one["hostname"] == "PC-TESTE"
    assert (await client.delete(f"/api/v1/agents/{agent.agent_id}", headers=admin)).status_code == 204
    assert (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=admin)).status_code == 404
    b = await factory.tenant("Revenda B")
    hb = auth(await login(client, b.admin_email))
    assert (await client.get("/api/v1/agents", headers=hb)).json()["items"] == []


def _unused(_: Any) -> None:
    return None
