"""Agent WebSocket gateway, end to end: a real uvicorn server with the gateway app, a real WebSocket
client playing the agent, commands created through the portal API and delivered via LISTEN/NOTIFY."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from app.core.config import Settings
from app.gateway.main import create_app
from app.models import AgentHeartbeat, AgentPresence
from tests.agent_helpers import FakeAgent, enrolled_agent
from tests.conftest import Factory, auth, free_port, login


@pytest.fixture
async def gateway(test_settings: Settings, clean_db: None) -> AsyncIterator[str]:
    port = free_port()
    app = create_app(test_settings)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="websockets-sansio")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "gateway não subiu"
    await asyncio.wait_for(app.state.listener.connected.wait(), 10)
    yield f"ws://127.0.0.1:{port}/ws/agent"
    server.should_exit = True
    await asyncio.wait_for(task, 15)


async def ws_connect(url: str, agent: FakeAgent) -> ClientConnection:
    return await connect(url, additional_headers={"Authorization": f"Bearer {agent.token}"})


async def recv(ws: ClientConnection, kind: str, wait_s: float = 10) -> dict[str, Any]:
    """Next message of the given type (others are skipped)."""
    async with asyncio.timeout(wait_s):
        while True:
            msg: dict[str, Any] = json.loads(await ws.recv())
            if msg["type"] == kind:
                data: dict[str, Any] = msg["data"]
                return data


async def send(ws: ClientConnection, kind: str, data: dict[str, Any]) -> None:
    await ws.send(json.dumps({"v": 1, "type": kind, "data": data}))


def heartbeat_body(**extra: Any) -> dict[str, Any]:
    return {
        "v": 1,
        "ts": datetime.now(UTC).isoformat(),
        "version": "1.0.0-test",
        "hostname": "PC-WS",
        **extra,
    }


async def test_rejects_missing_or_invalid_token(gateway: str) -> None:
    for headers in ({}, {"Authorization": "Bearer nao-e-um-jwt"}):
        async with connect(gateway, additional_headers=headers) as ws:
            with pytest.raises(ConnectionClosed) as exc:
                await ws.recv()
            assert exc.value.rcvd is not None
            assert exc.value.rcvd.code == 4401


async def test_heartbeat_presence_and_live_command(
    gateway: str,
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    agent = await enrolled_agent(client, tenant)
    async with await ws_connect(gateway, agent) as ws:
        welcome = await recv(ws, "welcome")
        assert welcome["agent_id"] == agent.agent_id
        assert welcome["heartbeat_seconds"] == 30
        await send(ws, "hello", {"v": 1, "version": "1.0.0-test", "capabilities": ["reconnect"]})

        await send(ws, "heartbeat", heartbeat_body(latency_ms=12.5, ws_connected=True))
        ack = await recv(ws, "heartbeat_ack")
        assert ack["cluster_role"] == "master"
        assert ack["config_version"] == 1
        info = (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=auth(admin))).json()
        assert info["ws_connected"] is True
        assert info["state"] == "online"
        assert info["hostname"] == "PC-WS"
        async with sessionmaker() as s:
            hb = (await s.execute(select(AgentHeartbeat).where(AgentHeartbeat.channel == "ws"))).scalar_one()
            assert hb.latency_ms == 12.5
            pres = await s.get(AgentPresence, uuid.UUID(agent.agent_id))
            assert pres is not None
            assert pres.latency_ms == 12.5

        # Comando criado no portal chega na hora pelo LISTEN/NOTIFY.
        created = (
            await client.post(
                f"/api/v1/agents/{agent.agent_id}/commands",
                json={"type": "snmp_test", "params": {"ip": "192.168.0.10"}},
                headers=auth(admin),
            )
        ).json()
        cmd = await recv(ws, "command", wait_s=5)
        assert cmd["id"] == created["id"]
        assert cmd["type"] == "snmp_test"
        assert cmd["params"] == {"ip": "192.168.0.10", "port": 161}
        state = (await client.get(f"/api/v1/commands/{cmd['id']}", headers=auth(admin))).json()["state"]
        assert state == "sent"

        await send(
            ws, "command_update", {"v": 1, "id": cmd["id"], "state": "running", "progress": "testando"}
        )
        assert (await recv(ws, "command_update_ack"))["state"] == "running"
        await send(
            ws,
            "command_update",
            {"v": 1, "id": cmd["id"], "state": "succeeded", "result": {"answered": 1}},
        )
        assert (await recv(ws, "command_update_ack")) == {"id": cmd["id"], "state": "succeeded"}
        got = (await client.get(f"/api/v1/commands/{cmd['id']}", headers=auth(admin))).json()
        assert got["state"] == "succeeded"
        assert got["result"] == {"answered": 1}

        # Mensagens inválidas recebem erro, sem derrubar a conexão.
        await ws.send("isto não é json")
        assert (await recv(ws, "error"))["code"] == "invalid_message"
        await send(ws, "command_update", {"v": 1, "id": str(uuid.uuid4()), "state": "succeeded"})
        err = await recv(ws, "error")
        assert err["code"] == "not_found"

    # Desconectou: a presença some.
    for _ in range(50):
        async with sessionmaker() as s:
            if await s.get(AgentPresence, uuid.UUID(agent.agent_id)) is None:
                break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("presença não foi removida ao desconectar")
    info = (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=auth(admin))).json()
    assert info["ws_connected"] is False


async def test_pending_commands_are_delivered_on_connect(
    gateway: str, client: httpx.AsyncClient, factory: Factory
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    agent = await enrolled_agent(client, tenant)
    ids = []
    for ctype in ("diagnostics", "reconnect"):
        resp = await client.post(
            f"/api/v1/agents/{agent.agent_id}/commands", json={"type": ctype}, headers=auth(admin)
        )
        ids.append(resp.json()["id"])
    async with await ws_connect(gateway, agent) as ws:
        got = [(await recv(ws, "command"))["id"], (await recv(ws, "command"))["id"]]
        assert got == ids  # na ordem de criação
        # Cancelado no portal depois de entregue: o gateway avisa o coletor.
        await client.post(f"/api/v1/commands/{ids[0]}/cancel", headers=auth(admin))
        assert (await recv(ws, "cancel", wait_s=5))["id"] == ids[0]


async def test_new_connection_replaces_old_and_revoke_closes(
    gateway: str,
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    agent = await enrolled_agent(client, tenant)
    first = await ws_connect(gateway, agent)
    await recv(first, "welcome")
    second = await ws_connect(gateway, agent)
    await recv(second, "welcome")
    with pytest.raises(ConnectionClosed) as exc:
        await asyncio.wait_for(first.recv(), 5)
    assert exc.value.rcvd is not None
    assert exc.value.rcvd.code == 4000
    async with sessionmaker() as s:
        count = (await s.execute(select(func.count()).select_from(AgentPresence))).scalar_one()
        assert count == 1

    await client.post(f"/api/v1/agents/{agent.agent_id}/revoke", headers=auth(admin))
    with pytest.raises(ConnectionClosed) as exc:
        await asyncio.wait_for(second.recv(), 5)
    assert exc.value.rcvd is not None
    assert exc.value.rcvd.code == 4403
    # Reconectar com o token antigo (ainda não expirado) também é recusado.
    async with await ws_connect(gateway, agent) as again:
        with pytest.raises(ConnectionClosed) as exc:
            await again.recv()
        assert exc.value.rcvd is not None
        assert exc.value.rcvd.code == 4403
