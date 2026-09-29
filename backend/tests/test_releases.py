"""Signed releases, manual update/rollback commands and automatic updates (PROMPT 5.2)."""

import hashlib
import uuid
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import Agent, Command
from app.services import updates
from tests.agent_helpers import FakeAgent, enrolled_agent
from tests.conftest import Factory, Tenant, auth, login
from tests.release_helpers import sign


async def publish(
    client: httpx.AsyncClient,
    token: str,
    data: bytes,
    *,
    component: str = "agent",
    version: str = "1.1.0",
    os_: str = "windows",
    arch: str = "amd64",
    channel: str = "canary",
    rollout: int = 100,
    signature: str | None = None,
) -> httpx.Response:
    return await client.post(
        "/api/v1/releases",
        params={
            "component": component,
            "version": version,
            "os": os_,
            "arch": arch,
            "channel": channel,
            "rollout_percent": rollout,
            "signature": signature or sign(component, version, os_, arch, data),
            "notes": "correções",
        },
        content=data,
        headers={**auth(token), "Content-Type": "application/octet-stream"},
    )


async def superadmin(client: httpx.AsyncClient, factory: Factory, t: Tenant) -> str:
    _, email = await factory.user(t.reseller_id, role="superadmin")
    return await login(client, email)


async def test_publish_checks_the_ed25519_signature(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    root = await superadmin(client, factory, t)
    admin = await login(client, t.admin_email)
    data = b"MZ-binario-do-coletor-1.1.0"

    assert (await publish(client, admin, data)).status_code == 403  # só a plataforma publica

    tampered = await publish(
        client, root, data + b"!", signature=sign("agent", "1.1.0", "windows", "amd64", data)
    )
    assert tampered.status_code == 400
    assert tampered.json()["detail"]["code"] == "invalid_signature"
    relabelled = await publish(
        client, root, data, version="1.2.0", signature=sign("agent", "1.1.0", "windows", "amd64", data)
    )
    assert relabelled.json()["detail"]["code"] == "invalid_signature"  # assinatura não vale para outra versão
    other_key = await publish(
        client,
        root,
        data,
        signature=sign("agent", "1.1.0", "windows", "amd64", data, Ed25519PrivateKey.generate()),
    )
    assert other_key.json()["detail"]["code"] == "invalid_signature"
    bad_version = await publish(client, root, data, version="1.1")
    assert bad_version.json()["detail"]["code"] == "invalid_version"

    ok = await publish(client, root, data)
    assert ok.status_code == 201, ok.text
    rel = ok.json()
    assert rel["sha256"] == hashlib.sha256(data).hexdigest()
    assert (rel["size_bytes"], rel["channel"], rel["rollout_percent"]) == (len(data), "canary", 100)
    assert (await publish(client, root, data)).json()["detail"]["code"] == "release_exists"

    listed = (await client.get("/api/v1/releases", headers=auth(admin))).json()
    assert [r["version"] for r in listed] == ["1.1.0"]

    patched = await client.patch(
        f"/api/v1/releases/{rel['id']}", json={"channel": "stable", "rollout_percent": 25}, headers=auth(root)
    )
    assert patched.status_code == 200, patched.text
    assert (patched.json()["channel"], patched.json()["rollout_percent"]) == ("stable", 25)
    assert (
        await client.patch(f"/api/v1/releases/{rel['id']}", json={"yanked": True}, headers=auth(admin))
    ).status_code == 403


async def test_manual_update_resolves_the_release_for_the_pc(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant()
    root = await superadmin(client, factory, t)
    admin = await login(client, t.admin_email)
    agent = await enrolled_agent(client, t)
    await agent.heartbeat(version="1.0.0")  # arch amd64, kind windows
    await agent.watchdog_heartbeat(version="1.0.0")
    data = b"coletor-1.1.0-windows-amd64"
    rel = (await publish(client, root, data)).json()
    wd_data = b"watchdog-1.0.3"
    wd = (await publish(client, root, wd_data, component="watchdog", version="1.0.3")).json()
    other = (await publish(client, root, b"linux-arm", os_="linux", arch="arm", version="1.1.0")).json()

    cmd = await client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        json={"type": "update", "params": {"version": "1.1.0"}},
        headers=auth(admin),
    )
    assert cmd.status_code == 201, cmd.text
    body = cmd.json()
    assert body["target"] == "watchdog"  # o watchdog troca o binário do coletor
    params = body["params"]
    assert params["release_id"] == rel["id"]
    assert (params["sha256"], params["os"], params["arch"]) == (rel["sha256"], "windows", "amd64")
    assert params["url"] == f"/api/agent/releases/{rel['id']}/file"

    # O coletor baixa com o próprio token; binário de outro alvo não é servido a este PC.
    file = await client.get(params["url"], headers=agent.headers)
    assert file.status_code == 200
    assert file.content == data
    assert (
        await client.get(f"/api/agent/releases/{other['id']}/file", headers=agent.headers)
    ).status_code == 404
    assert (await client.get(params["url"])).status_code == 401

    wd_cmd = await client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        json={"type": "update", "params": {"version": "1.0.3", "component": "watchdog"}},
        headers=auth(admin),
    )
    assert wd_cmd.json()["target"] == "agent"  # processo inverso: o coletor atualiza o watchdog
    assert wd_cmd.json()["params"]["release_id"] == wd["id"]

    missing = await client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        json={"type": "update", "params": {"version": "9.9.9"}},
        headers=auth(admin),
    )
    assert missing.json()["detail"]["code"] == "release_not_found"
    await client.patch(f"/api/v1/releases/{rel['id']}", json={"yanked": True}, headers=auth(root))
    yanked = await client.post(
        f"/api/v1/agents/{agent.agent_id}/commands",
        json={"type": "update", "params": {"version": "1.1.0"}},
        headers=auth(admin),
    )
    assert yanked.json()["detail"]["code"] == "release_yanked"
    assert (await client.get(params["url"], headers=agent.headers)).status_code == 404


def test_rollout_bucket_is_stable_and_proportional() -> None:
    rid = uuid.uuid4()
    agents = [uuid.uuid4() for _ in range(2000)]
    assert all(updates.in_rollout(a, rid, 100) for a in agents)
    assert not any(updates.in_rollout(a, rid, 0) for a in agents)
    half = sum(updates.in_rollout(a, rid, 50) for a in agents)
    assert 850 < half < 1150
    assert [updates.in_rollout(a, rid, 30) for a in agents[:50]] == [
        updates.in_rollout(a, rid, 30) for a in agents[:50]
    ]


async def _agent_with_channel(
    client: httpx.AsyncClient,
    t: Tenant,
    maker: async_sessionmaker[AsyncSession],
    name: str,
    channel: str,
    *,
    watchdog: bool = True,
) -> FakeAgent:
    agent = await enrolled_agent(client, t, name=name)
    await agent.heartbeat(version="1.0.0")
    if watchdog:
        await agent.watchdog_heartbeat(version="1.0.0")
    async with maker() as s:
        await s.execute(
            update(Agent).where(Agent.id == uuid.UUID(agent.agent_id)).values(update_channel=channel)
        )
        await s.commit()
    return agent


async def _updates(maker: async_sessionmaker[AsyncSession]) -> dict[tuple[str, str], dict[str, Any]]:
    async with maker() as s:
        rows = (await s.execute(select(Command).where(Command.type == "update"))).scalars()
        return {
            (str(c.agent_id), c.params["component"]): {"target": c.target, **c.params, "id": str(c.id)}
            for c in rows
        }


async def test_automatic_updates_follow_channel_rollout_and_canary_failures(
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
) -> None:
    t = await factory.tenant()
    root = await superadmin(client, factory, t)
    canary = await _agent_with_channel(client, t, sessionmaker, "Canary 1", "canary")
    stable = await _agent_with_channel(client, t, sessionmaker, "Estável", "stable")
    no_wd = await _agent_with_channel(client, t, sessionmaker, "Sem vigia", "stable", watchdog=False)
    beta = (await publish(client, root, b"agente-1.2.0", version="1.2.0", channel="canary")).json()
    ga = (await publish(client, root, b"agente-1.1.0", version="1.1.0", channel="stable")).json()
    older = (await publish(client, root, b"agente-0.9.0", version="0.9.0", channel="stable")).json()
    wd = (
        await publish(client, root, b"vigia-1.0.4", component="watchdog", version="1.0.4", channel="stable")
    ).json()

    async with sessionmaker() as s:
        created = await updates.offer_updates(s, test_settings)
        await s.commit()
    offered = await _updates(sessionmaker)
    # canary pega a mais nova dos dois canais; estável só a do canal estável; nunca versão mais velha.
    assert offered[(canary.agent_id, "agent")]["release_id"] == beta["id"]
    assert offered[(canary.agent_id, "agent")]["target"] == "watchdog"
    assert offered[(stable.agent_id, "agent")]["release_id"] == ga["id"]
    assert (no_wd.agent_id, "agent") not in offered  # sem watchdog vivo, ninguém para executar
    assert offered[(stable.agent_id, "watchdog")]["target"] == "agent"  # o coletor atualiza o watchdog
    assert offered[(stable.agent_id, "watchdog")]["release_id"] == wd["id"]
    assert older["id"] not in {o["release_id"] for o in offered.values()}
    assert created == len(offered)

    async with sessionmaker() as s:  # uma atualização por vez: nada novo enquanto a anterior está aberta
        assert await updates.offer_updates(s, test_settings) == 0

    # A atualização falhou no canary (o watchdog fez rollback): 100% de falha > 5%.
    await canary.report(
        offered[(canary.agent_id, "agent")]["id"],
        "failed",
        error="não ficou saudável em 2 min; rollback feito",
    )
    listed = {r["id"]: r for r in (await client.get("/api/v1/releases", headers=auth(root))).json()}
    assert listed[beta["id"]]["canary_failure_percent"] == 100.0
    assert listed[beta["id"]]["auto_update_blocked"] is True
    assert listed[beta["id"]]["updates_failed"] == 1

    canary2 = await _agent_with_channel(client, t, sessionmaker, "Canary 2", "canary")
    async with sessionmaker() as s:
        await updates.offer_updates(s, test_settings)
        await s.commit()
    offered = await _updates(sessionmaker)
    # A versão ruim não é oferecida a mais ninguém; o canary 2 recebe a estável.
    assert offered[(canary2.agent_id, "agent")]["release_id"] == ga["id"]


async def test_rollout_zero_and_auto_update_off_offer_nothing(
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
) -> None:
    t = await factory.tenant()
    root = await superadmin(client, factory, t)
    await _agent_with_channel(client, t, sessionmaker, "Estável", "stable")
    await publish(client, root, b"agente-1.3.0", version="1.3.0", channel="stable", rollout=0)
    async with sessionmaker() as s:
        assert await updates.offer_updates(s, test_settings) == 0
        off = test_settings.model_copy(update={"auto_update": False})
        await publish(client, root, b"agente-1.4.0", version="1.4.0", channel="stable")
        assert await updates.offer_updates(s, off) == 0
