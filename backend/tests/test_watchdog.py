"""dm-watchdog channel (PROMPT 5.1), its commands (4.7) and the Reactivate button's step 2."""

import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Agent, Command
from tests.agent_helpers import FakeAgent, enrolled_agent
from tests.conftest import Factory, auth, login


async def send(
    client: httpx.AsyncClient, token: str, agent_id: str, ctype: str, params: dict[str, Any] | None = None
) -> httpx.Response:
    return await client.post(
        f"/api/v1/agents/{agent_id}/commands",
        json={"type": ctype, "params": params or {}},
        headers=auth(token),
    )


async def agent_out(client: httpx.AsyncClient, token: str, agent_id: str) -> dict[str, Any]:
    resp = await client.get(f"/api/v1/agents/{agent_id}", headers=auth(token))
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()
    return data


async def test_watchdog_heartbeat_records_state_restarts_and_rollback_version(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)

    # Sem watchdog nunca visto, comando do watchdog é recusado com motivo claro (não fica pendente à toa).
    refused = await send(client, admin, agent.agent_id, "restart_agent")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "watchdog_never_seen"
    assert (await agent_out(client, admin, agent.agent_id))["watchdog_alive"] is False

    at = datetime.now(UTC) - timedelta(seconds=40)
    resp = await agent.watchdog_heartbeat(
        version="1.0.1",
        agent_state="running",
        agent_version="1.2.0",
        agent_memory_bytes=31_000_000,
        previous_agent_version="1.1.0",
        restarts=[{"at": at.isoformat(), "reason": "/health sem resposta 3 vezes seguidas"}],
    )
    assert resp["commands"] == []
    out = await agent_out(client, admin, agent.agent_id)
    assert out["watchdog_alive"] is True
    assert out["watchdog_version"] == "1.0.1"
    status = out["watchdog_status"]
    assert status["agent_state"] == "running"
    assert status["previous_agent_version"] == "1.1.0"
    assert status["agent_memory_bytes"] == 31_000_000
    assert [r["reason"] for r in status["restarts"]] == ["/health sem resposta 3 vezes seguidas"]

    await agent.watchdog_heartbeat(
        restarts=[{"at": datetime.now(UTC).isoformat(), "reason": "memória acima de 300 MB (412 MB)"}]
    )
    status = (await agent_out(client, admin, agent.agent_id))["watchdog_status"]
    assert [r["reason"] for r in status["restarts"]] == [
        "/health sem resposta 3 vezes seguidas",
        "memória acima de 300 MB (412 MB)",
    ]

    # Vigilância mútua: o coletor também informa o que vê do serviço do watchdog.
    await agent.heartbeat(watchdog_state="stopped")
    assert (await agent_out(client, admin, agent.agent_id))["watchdog_status"]["service_state"] == "stopped"


async def test_watchdog_commands_go_only_to_the_watchdog(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)
    await agent.watchdog_heartbeat()

    restart = (await send(client, admin, agent.agent_id, "restart_agent")).json()
    reconnect = (await send(client, admin, agent.agent_id, "reconnect")).json()
    logs = (await send(client, admin, agent.agent_id, "get_logs", {"hours": 2, "source": "watchdog"})).json()
    assert (restart["target"], reconnect["target"], logs["target"]) == ("watchdog", "agent", "watchdog")
    assert restart["type_label"] == "Reiniciar o coletor (pelo watchdog)"

    pending = await client.get("/api/agent/commands/pending", headers=agent.headers)
    assert [c["id"] for c in pending.json()["commands"]] == [reconnect["id"]]

    delivered = await agent.watchdog_heartbeat()
    assert {c["id"] for c in delivered["commands"]} == {restart["id"], logs["id"]}
    assert (await agent.watchdog_heartbeat())["commands"] == []  # entregue: não repete antes de 20 s

    await agent.report(restart["id"], "succeeded", result={"restarted": True, "took_seconds": 4.2})
    got = (await client.get(f"/api/v1/commands/{restart['id']}", headers=auth(admin))).json()
    assert got["state"] == "succeeded"
    assert got["result"] == {"restarted": True, "took_seconds": 4.2}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("watchdog.log", '{"msg":"agente reiniciado"}\n')
    up = await client.post(
        "/api/agent/uploads/logs",
        params={"command_id": logs["id"]},
        content=buf.getvalue(),
        headers={**agent.headers, "Content-Type": "application/zip"},
    )
    assert up.status_code == 200, up.text
    listed = (await client.get(f"/api/v1/agents/{agent.agent_id}/logs", headers=auth(admin))).json()
    assert [(row["source"], row["command_id"]) for row in listed] == [("watchdog", logs["id"])]


async def test_rollback_needs_a_saved_version(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)
    await agent.watchdog_heartbeat(previous_agent_version="")
    refused = await send(client, admin, agent.agent_id, "rollback")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "no_previous_version"

    await agent.watchdog_heartbeat(previous_agent_version="1.4.2")
    ok = await send(client, admin, agent.agent_id, "rollback")
    assert ok.status_code == 201, ok.text
    assert ok.json()["target"] == "watchdog"
    assert ok.json()["params"] == {"component": "agent", "expected_version": "1.4.2"}
    async with sessionmaker() as s:
        assert (await s.get(Command, uuid.UUID(ok.json()["id"]))) is not None


async def test_uninstall_requires_admin_and_double_confirmation(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t, name="PC da recepção")
    admin = await login(client, t.admin_email)
    await agent.watchdog_heartbeat()
    _, op_email = await factory.user(t.reseller_id, role="operator")
    operator = await login(client, op_email)

    denied = await send(client, operator, agent.agent_id, "uninstall", {"confirm_name": "PC da recepção"})
    assert denied.status_code == 403
    wrong = await send(client, admin, agent.agent_id, "uninstall", {"confirm_name": "PC da recepcao"})
    assert wrong.status_code == 400
    assert wrong.json()["detail"]["code"] == "confirmation_mismatch"
    ok = await send(client, admin, agent.agent_id, "uninstall", {"confirm_name": "PC da recepção"})
    assert ok.status_code == 201, ok.text
    assert ok.json()["target"] == "watchdog"


async def _make_offline(
    maker: async_sessionmaker[AsyncSession], agent_id: str, *, watchdog_seen: datetime | None
) -> None:
    async with maker() as s:
        await s.execute(
            update(Agent)
            .where(Agent.id == uuid.UUID(agent_id))
            .values(
                state="offline",
                last_seen_at=datetime.now(UTC) - timedelta(minutes=12),
                last_watchdog_seen_at=watchdog_seen,
            )
        )
        await s.commit()


async def test_reactivate_offline_agent_with_live_watchdog_restarts_it(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent: FakeAgent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)
    await agent.watchdog_heartbeat()
    await _make_offline(sessionmaker, agent.agent_id, watchdog_seen=datetime.now(UTC) - timedelta(seconds=50))

    resp = await client.post(f"/api/v1/agents/{agent.agent_id}/reactivate", headers=auth(admin))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "watchdog_restart"
    assert body["wait_seconds"] == 180
    # Horário do servidor: o portal mede a volta do coletor contra ele (não contra o relógio do navegador).
    assert abs(datetime.fromisoformat(body["requested_at"]) - datetime.now(UTC)) < timedelta(seconds=30)
    assert [s["action"] for s in body["steps"]] == ["restart_agent"]
    async with sessionmaker() as s:
        cmd = (
            await s.execute(select(Command).where(Command.id == uuid.UUID(body["steps"][0]["command_id"])))
        ).scalar_one()
        assert (cmd.type, cmd.target, cmd.state) == ("restart_agent", "watchdog", "pending")
    delivered = await agent.watchdog_heartbeat()
    assert [c["type"] for c in delivered["commands"]] == ["restart_agent"]


async def test_reactivate_with_dead_watchdog_falls_back_to_diagnosis(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)
    await agent.watchdog_heartbeat()
    await _make_offline(sessionmaker, agent.agent_id, watchdog_seen=datetime.now(UTC) - timedelta(minutes=8))
    body = (await client.post(f"/api/v1/agents/{agent.agent_id}/reactivate", headers=auth(admin))).json()
    assert body["outcome"] == "nothing_online"
    async with sessionmaker() as s:
        restarts = (await s.execute(select(Command).where(Command.type == "restart_agent"))).scalars().all()
        assert restarts == []  # vigia sem sinal há 8 min: pedir reinício a ele seria em vão
