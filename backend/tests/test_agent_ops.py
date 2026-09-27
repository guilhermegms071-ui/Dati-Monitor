"""Collector screens (PROMPT 10.5): Reativar (PROMPT 4.7), cluster, heartbeat series, versions, bulk
commands and the log viewer."""

import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Agent, AuditLog
from tests.agent_helpers import enrolled_agent
from tests.conftest import Factory, auth, login


async def commands_of(client: httpx.AsyncClient, token: str, agent_id: str) -> list[str]:
    items = (await client.get(f"/api/v1/agents/{agent_id}/commands", headers=auth(token))).json()["items"]
    return sorted(c["type"] for c in items)


async def test_reactivate_three_outcomes(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    first = await enrolled_agent(client, tenant, "PC da recepção")
    second = await enrolled_agent(client, tenant, "PC do financeiro")
    await first.heartbeat(host_mac="00:11:22:33:44:55")
    await second.heartbeat()

    # 1. Coletor vivo: reconnect + read_now para ele.
    r = (await client.post(f"/api/v1/agents/{first.agent_id}/reactivate", headers=auth(admin))).json()
    assert r["outcome"] == "commands_sent"
    assert [s["action"] for s in r["steps"]] == ["reconnect", "read_now"]
    assert all(s["command_id"] for s in r["steps"])
    assert await commands_of(client, admin, first.agent_id) == ["read_now", "reconnect"]

    # 3. Coletor caído, outro do local vivo: o outro assume e manda Wake-on-LAN.
    async with sessionmaker() as s:
        old = datetime.now(UTC) - timedelta(minutes=10)
        await s.execute(
            update(Agent)
            .where(Agent.id == uuid.UUID(first.agent_id))
            .values(last_seen_at=old, state="offline")
        )
        await s.commit()
    r = (await client.post(f"/api/v1/agents/{first.agent_id}/reactivate", headers=auth(admin))).json()
    assert r["outcome"] == "failover"
    assert [s["action"] for s in r["steps"]] == ["promote_master", "wake_host"]
    assert all(s["agent_id"] == second.agent_id for s in r["steps"])
    assert await commands_of(client, admin, second.agent_id) == ["promote_master", "wake_host"]
    wake = next(s for s in r["steps"] if s["action"] == "wake_host")
    cmd = (await client.get(f"/api/v1/commands/{wake['command_id']}", headers=auth(admin))).json()
    assert cmd["params"]["mac"] == "00:11:22:33:44:55"

    # 4. Ninguém vivo: diagnóstico claro, sem comandos.
    async with sessionmaker() as s:
        await s.execute(
            update(Agent).values(last_seen_at=datetime(2026, 9, 20, 11, 30, tzinfo=UTC), state="offline")
        )
        await s.commit()
    r = (await client.post(f"/api/v1/agents/{first.agent_id}/reactivate", headers=auth(admin))).json()
    assert r["outcome"] == "nothing_online"
    assert r["steps"] == []
    assert "Nenhum coletor deste local está ligado. Último sinal: 20/09 08:30" in r["message"]
    assert len(r["suggestions"]) == 3
    async with sessionmaker() as s:
        outcomes = list(
            (await s.execute(select(AuditLog.after).where(AuditLog.action == "agent.reactivate"))).scalars()
        )
    assert sorted(o["outcome"] for o in outcomes if o) == ["commands_sent", "failover", "nothing_online"]

    _, email = await factory.user(tenant.reseller_id, role="customer_viewer", customer_id=tenant.customer_id)
    viewer = await login(client, email)
    assert (
        await client.post(f"/api/v1/agents/{first.agent_id}/reactivate", headers=auth(viewer))
    ).status_code == 403


async def test_cluster_series_versions_and_bulk(client: httpx.AsyncClient, factory: Factory) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    first = await enrolled_agent(client, tenant, "PC A")
    second = await enrolled_agent(client, tenant, "PC B")
    await first.heartbeat(cpu_percent=3.5, memory_bytes=40_000_000, queue_pending=2, version="1.0.0")
    await first.heartbeat(cpu_percent=4.0, memory_bytes=41_000_000, version="1.1.0")
    await second.heartbeat()

    listed = (await client.get("/api/v1/agents", headers=auth(admin))).json()["items"]
    assert {(a["name"], a["customer_name"], a["site_name"]) for a in listed} == {
        ("PC A", "Revenda A Cliente", "Revenda A Local"),
        ("PC B", "Revenda A Cliente", "Revenda A Local"),
    }
    assert listed[0]["customer_id"] == str(tenant.customer_id)
    cluster = (await client.get(f"/api/v1/sites/{tenant.site_id}/cluster", headers=auth(admin))).json()
    roles = {m["name"]: m["cluster_role"] for m in cluster["members"]}
    assert roles == {"PC A": "master", "PC B": "standby"}
    assert cluster["master_agent_id"] == first.agent_id
    assert cluster["events"][0]["reason"] == "first_agent"

    hb = (await client.get(f"/api/v1/agents/{first.agent_id}/heartbeats", headers=auth(admin))).json()
    assert [h["cpu_percent"] for h in hb] == [3.5, 4.0]
    assert hb[0]["queue_pending"] == 2
    versions = (await client.get(f"/api/v1/agents/{first.agent_id}/versions", headers=auth(admin))).json()
    assert [v["version"] for v in versions] == ["1.1.0", "1.0.0"]

    res = await client.post(
        "/api/v1/agents/commands/bulk",
        json={
            "agent_ids": [first.agent_id, second.agent_id, str(uuid.uuid4())],
            "command": {"type": "diagnostics"},
        },
        headers=auth(admin),
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert [bool(r["command_id"]) for r in out] == [True, True, False]
    assert out[2]["error"] == "Coletor não encontrado(a)"
    other = await factory.tenant("Revenda B")
    other_admin = await login(client, other.admin_email)
    assert (
        await client.get(f"/api/v1/sites/{tenant.site_id}/cluster", headers=auth(other_admin))
    ).status_code == 404


async def test_log_viewer_tail(client: httpx.AsyncClient, factory: Factory) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    agent = await enrolled_agent(client, tenant)
    cmd = (
        await client.post(
            f"/api/v1/agents/{agent.agent_id}/commands", json={"type": "get_logs"}, headers=auth(admin)
        )
    ).json()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("agent-antigo.log", (2026, 9, 1, 0, 0, 0)), "antigo\n")
        zf.writestr(
            zipfile.ZipInfo("agent.log", (2026, 9, 25, 10, 0, 0)),
            "".join(f"linha {i} ação\n" for i in range(1, 101)),
        )
    up = await client.post(
        f"/api/agent/uploads/logs?command_id={cmd['id']}",
        content=buf.getvalue(),
        headers={**agent.headers, "Content-Type": "application/zip"},
    )
    log_id = up.json()["id"]
    tail = await client.get(f"/api/v1/agent-logs/{log_id}/tail", params={"lines": 10}, headers=auth(admin))
    assert tail.status_code == 200
    lines = tail.text.splitlines()
    assert lines == [f"linha {i} ação" for i in range(91, 101)]
    assert tail.headers["content-type"].startswith("text/plain")
