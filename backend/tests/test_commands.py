"""Remote commands (PROMPT 4.7): creation rules, delivery over the HTTPS contingency channel, idempotent
updates, cancellation, expiry, uploads and presence."""

import asyncio
import gzip
import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.security import derive_agent_key, set_server_signature
from app.gateway.listener import asyncpg_dsn
from app.models import Agent, AgentPresence, AuditLog, ClusterEvent, Command, Device, Site
from app.services import commands as commands_svc
from app.services import presence as presence_svc
from tests.agent_helpers import FakeAgent, create_agent, enrolled_agent
from tests.conftest import Factory, Tenant, auth, login


async def send_command(
    client: httpx.AsyncClient,
    token: str,
    agent_id: str,
    ctype: str,
    params: dict[str, Any] | None = None,
    **kw: Any,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/agents/{agent_id}/commands",
        json={"type": ctype, "params": params or {}, **kw},
        headers=auth(token),
    )


async def pending(agent: FakeAgent) -> list[dict[str, Any]]:
    resp = await agent.client.get("/api/agent/commands/pending", headers=agent.headers)
    assert resp.status_code == 200, resp.text
    cmds: list[dict[str, Any]] = resp.json()["commands"]
    return cmds


async def report(agent: FakeAgent, cid: str, state: str, **extra: Any) -> httpx.Response:
    return await agent.client.post(
        f"/api/agent/commands/{cid}/update",
        json={"v": 1, "id": cid, "state": state, **extra},
        headers=agent.headers,
    )


async def setup(client: httpx.AsyncClient, factory: Factory) -> tuple[Tenant, str, FakeAgent]:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    agent = await enrolled_agent(client, tenant)
    return tenant, admin, agent


async def test_command_lifecycle_over_https(client: httpx.AsyncClient, factory: Factory) -> None:
    _, admin, agent = await setup(client, factory)
    resp = await send_command(client, admin, agent.agent_id, "diagnostics")
    assert resp.status_code == 201, resp.text
    cmd = resp.json()
    assert cmd["state"] == "pending"
    assert cmd["type"] == "diagnostics"
    expires = datetime.fromisoformat(cmd["expires_at"]) - datetime.fromisoformat(cmd["created_at"])
    assert timedelta(minutes=9) < expires <= timedelta(minutes=10, seconds=5)

    delivered = await pending(agent)
    assert [c["id"] for c in delivered] == [cmd["id"]]
    assert await pending(agent) == []  # entregue e ainda sem confirmação: não repete antes de 20 s

    assert (await report(agent, cmd["id"], "acked")).json()["state"] == "acked"
    assert (await report(agent, cmd["id"], "running", progress="testando DNS")).json()["state"] == "running"
    done = await report(agent, cmd["id"], "succeeded", result={"dns": {"ok": True}}, output="tudo certo")
    assert done.json() == {"v": 1, "id": cmd["id"], "state": "succeeded"}
    # Repetido (o agente reenvia se não viu a confirmação) e tardio: ignorados, estado final mantido.
    assert (await report(agent, cmd["id"], "succeeded")).json()["state"] == "succeeded"
    assert (await report(agent, cmd["id"], "failed", error="tarde demais")).json()["state"] == "succeeded"
    assert (await report(agent, cmd["id"], "running")).json()["state"] == "succeeded"

    got = (await client.get(f"/api/v1/commands/{cmd['id']}", headers=auth(admin))).json()
    assert got["state"] == "succeeded"
    assert got["type_label"] == "Diagnóstico"  # o portal mostra o nome em português
    assert got["result"] == {"dns": {"ok": True}}
    assert got["output"] == "tudo certo"
    assert got["progress"] == "testando DNS"
    for field in ("sent_at", "acked_at", "started_at", "finished_at"):
        assert got[field], field

    listed = (await client.get(f"/api/v1/agents/{agent.agent_id}/commands", headers=auth(admin))).json()
    assert [c["id"] for c in listed["items"]] == [cmd["id"]]


async def test_failed_command_keeps_error_and_output_is_capped(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    _, admin, agent = await setup(client, factory)
    cmd = (await send_command(client, admin, agent.agent_id, "read_device", {"ip": "192.168.0.50"})).json()
    assert cmd["params"] == {"ip": "192.168.0.50", "port": 161}
    await pending(agent)
    big = "é" * (commands_svc.OUTPUT_LIMIT)  # 2 bytes por caractere em UTF-8
    resp = await report(agent, cmd["id"], "failed", error="nenhuma credencial respondeu", output=big)
    assert resp.json()["state"] == "failed"
    got = (await client.get(f"/api/v1/commands/{cmd['id']}", headers=auth(admin))).json()
    assert got["result"] == {"error": "nenhuma credencial respondeu"}
    assert len(got["output"].encode()) <= commands_svc.OUTPUT_LIMIT
    assert got["output"].startswith("éé")


async def test_parameter_validation(client: httpx.AsyncClient, factory: Factory) -> None:
    _, admin, agent = await setup(client, factory)
    bad: list[tuple[str, dict[str, Any]]] = [
        ("read_device", {"ip": "8.8.8.8"}),  # fora da rede local do cliente
        ("read_device", {"ip": "999.1.1.1"}),
        ("read_device", {"ip": "192.168.0.1", "port": 70000}),
        ("mib_walk", {"ip": "10.0.0.1", "root_oid": "abc"}),
        ("get_logs", {"hours": 500}),
        ("ping_host", {"ip": "192.168.0.1", "ports": [0]}),
        ("diagnostics", {"inesperado": 1}),
        ("scan_now", {"range_id": str(uuid.uuid4())}),
        ("read_now", {"device_ids": [str(uuid.uuid4())]}),
        ("wake_host", {"target_agent_id": agent.agent_id}),
    ]
    for ctype, params in bad:
        resp = await send_command(client, admin, agent.agent_id, ctype, params)
        assert resp.status_code == 400, (ctype, params, resp.text)
        assert resp.json()["detail"]["code"] in {
            "invalid_params",
            "range_not_in_site",
            "devices_not_in_site",
            "target_is_self",
        }, resp.text
    unknown = await send_command(client, admin, agent.agent_id, "formatar_disco")
    assert unknown.status_code == 422
    walk = await send_command(
        client, admin, agent.agent_id, "mib_walk", {"ip": "10.0.0.9", "root_oid": ".1.3.6.1.2.1.43"}
    )
    assert walk.status_code == 201
    assert walk.json()["params"] == {
        "ip": "10.0.0.9",
        "port": 161,
        "root_oid": "1.3.6.1.2.1.43",
        "device_id": None,
    }


async def test_permissions_and_scope(client: httpx.AsyncClient, factory: Factory) -> None:
    tenant, admin, agent = await setup(client, factory)
    _, tech_email = await factory.user(tenant.reseller_id, role="technician")
    _, viewer_email = await factory.user(
        tenant.reseller_id, role="customer_viewer", customer_id=tenant.customer_id
    )
    tech = await login(client, tech_email)
    viewer = await login(client, viewer_email)
    assert (
        await send_command(client, tech, agent.agent_id, "ping_host", {"ip": "192.168.0.1"})
    ).status_code == 201
    assert (await send_command(client, viewer, agent.agent_id, "reconnect")).status_code == 403
    other = await factory.tenant("Revenda B")
    other_admin = await login(client, other.admin_email)
    assert (await send_command(client, other_admin, agent.agent_id, "reconnect")).status_code == 404
    cmd = (await send_command(client, admin, agent.agent_id, "reconnect")).json()
    assert (await client.get(f"/api/v1/commands/{cmd['id']}", headers=auth(other_admin))).status_code == 404
    assert (
        await client.post(f"/api/v1/commands/{cmd['id']}/cancel", headers=auth(viewer))
    ).status_code == 403
    # Outro coletor não consegue atualizar comando alheio.
    intruder = await enrolled_agent(client, other)
    assert (await report(intruder, cmd["id"], "succeeded")).status_code == 404


async def test_agent_not_enrolled_or_revoked(client: httpx.AsyncClient, factory: Factory) -> None:
    tenant, admin, agent = await setup(client, factory)
    fresh = await create_agent(client, admin, tenant.site_id, "Ainda não instalado")
    resp = await send_command(client, admin, fresh["agent"]["id"], "reconnect")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "agent_not_enrolled"
    await client.post(f"/api/v1/agents/{agent.agent_id}/revoke", headers=auth(admin))
    resp = await send_command(client, admin, agent.agent_id, "reconnect")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "agent_revoked"


async def test_cancel_and_expiry(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    _, admin, agent = await setup(client, factory)
    a = (await send_command(client, admin, agent.agent_id, "scan_now")).json()
    b = (await send_command(client, admin, agent.agent_id, "read_now", expires_in_minutes=1)).json()
    c = (await send_command(client, admin, agent.agent_id, "diagnostics")).json()
    assert {x["id"] for x in await pending(agent)} == {a["id"], b["id"], c["id"]}

    cancelled = await client.post(f"/api/v1/commands/{a['id']}/cancel", headers=auth(admin))
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    again = await client.post(f"/api/v1/commands/{a['id']}/cancel", headers=auth(admin))
    assert again.status_code == 409
    # O agente ainda pode mandar o resultado: é aceito e ignorado (o comando já terminou).
    assert (await report(agent, a["id"], "succeeded")).json()["state"] == "cancelled"

    async with sessionmaker() as s:
        assert await commands_svc.recently_cancelled(s, uuid.UUID(agent.agent_id)) == [uuid.UUID(a["id"])]
        await s.execute(
            update(Command)
            .where(Command.id == uuid.UUID(b["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        # c em execução há muito tempo, sem notícia do coletor.
        await s.execute(
            update(Command)
            .where(Command.id == uuid.UUID(c["id"]))
            .values(state="running", expires_at=datetime.now(UTC) - timedelta(hours=2))
        )
        await s.commit()
        assert await commands_svc.expire_commands(s) == 2
        await s.commit()
    got_b = (await client.get(f"/api/v1/commands/{b['id']}", headers=auth(admin))).json()
    got_c = (await client.get(f"/api/v1/commands/{c['id']}", headers=auth(admin))).json()
    assert got_b["state"] == "expired"
    assert got_c["state"] == "failed"
    assert "tempo esgotado" in got_c["result"]["error"]


async def test_unacknowledged_command_is_redelivered(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    _, admin, agent = await setup(client, factory)
    cmd = (await send_command(client, admin, agent.agent_id, "reconnect")).json()
    assert len(await pending(agent)) == 1
    async with sessionmaker() as s:
        await s.execute(
            update(Command)
            .where(Command.id == uuid.UUID(cmd["id"]))
            .values(sent_at=datetime.now(UTC) - timedelta(seconds=30))
        )
        await s.commit()
    assert [c["id"] for c in await pending(agent)] == [cmd["id"]]  # conexão caiu antes do ack: reenvia
    await report(agent, cmd["id"], "acked")
    async with sessionmaker() as s:
        await s.execute(
            update(Command)
            .where(Command.id == uuid.UUID(cmd["id"]))
            .values(sent_at=datetime.now(UTC) - timedelta(seconds=30))
        )
        await s.commit()
    assert await pending(agent) == []  # confirmado: não reenvia mais


async def test_pause_resume_are_server_side(client: httpx.AsyncClient, factory: Factory) -> None:
    _, admin, agent = await setup(client, factory)
    await agent.heartbeat()
    assert (await send_command(client, admin, agent.agent_id, "pause")).status_code == 201
    hb = await agent.heartbeat(paused=False)  # o coletor ainda não sabe: o servidor manda pausar
    assert hb["paused"] is True
    info = (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=auth(admin))).json()
    assert info["paused"] is True
    assert info["state"] == "paused"
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert cfg["paused"] is True
    await send_command(client, admin, agent.agent_id, "resume")
    hb = await agent.heartbeat(paused=True)
    assert hb["paused"] is False
    assert (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=auth(admin))).json()[
        "state"
    ] == "online"


async def test_promote_master_switches_the_site_lease(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tenant, admin, first = await setup(client, factory)
    second = await enrolled_agent(client, tenant, "Coletor 2")
    assert (await first.heartbeat())["cluster_role"] == "master"
    assert (await second.heartbeat())["cluster_role"] == "standby"
    resp = await send_command(client, admin, second.agent_id, "promote_master")
    assert resp.status_code == 201
    assert (await second.heartbeat())["cluster_role"] == "master"
    assert (await first.heartbeat())["cluster_role"] == "standby"
    async with sessionmaker() as s:
        site = await s.get(Site, tenant.site_id)
        assert site is not None
        assert str(site.master_agent_id) == second.agent_id
        ev = (
            await s.execute(select(ClusterEvent).where(ClusterEvent.reason == "manual_promote"))
        ).scalar_one()
        assert str(ev.from_agent_id) == first.agent_id
        assert str(ev.to_agent_id) == second.agent_id
        assert ev.created_by is not None


async def test_wake_host_resolves_the_other_pc_mac(client: httpx.AsyncClient, factory: Factory) -> None:
    tenant, admin, first = await setup(client, factory)
    second = await enrolled_agent(client, tenant, "Coletor 2")
    resp = await send_command(
        client, admin, first.agent_id, "wake_host", {"target_agent_id": second.agent_id}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "target_without_mac"
    await second.heartbeat(host_mac="00:11:22:33:44:55", local_ips=["192.168.10.21"])
    resp = await send_command(
        client, admin, first.agent_id, "wake_host", {"target_agent_id": second.agent_id}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["params"] == {
        "target_agent_id": second.agent_id,
        "mac": "00:11:22:33:44:55",
        "target_ips": ["192.168.10.21"],
    }
    other = await factory.tenant("Revenda B")
    foreign = await enrolled_agent(client, other)
    resp = await send_command(
        client, admin, first.agent_id, "wake_host", {"target_agent_id": foreign.agent_id}
    )
    assert resp.json()["detail"]["code"] == "target_not_in_site"


async def test_read_now_resolves_devices_of_the_site(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    _, admin, agent = await setup(client, factory)
    await agent.send([agent.reading("SER-1", {"total": 100, "mono": 100}, ip="192.168.10.50")])
    async with sessionmaker() as s:
        device = (await s.execute(select(Device).where(Device.serial == "SER-1"))).scalar_one()
    resp = await send_command(client, admin, agent.agent_id, "read_now", {"device_ids": [str(device.id)]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["params"] == {
        "devices": [{"id": str(device.id), "serial": "SER-1", "ip": "192.168.10.50", "port": 161}]
    }
    walk = await send_command(client, admin, agent.agent_id, "mib_walk", {"ip": "192.168.10.50"})
    assert walk.json()["params"]["device_id"] == str(device.id)
    # Outra impressora no MESMO IP, em outra porta SNMP (caso do simulador e de NAT): o walk escolhe pela porta.
    await agent.send([agent.reading("SER-2", {"total": 50, "mono": 50}, ip="192.168.10.50", port=1162)])
    async with sessionmaker() as s:
        other = (await s.execute(select(Device).where(Device.serial == "SER-2"))).scalar_one()
    walk = await send_command(
        client, admin, agent.agent_id, "mib_walk", {"ip": "192.168.10.50", "port": 1162}
    )
    assert walk.status_code == 201, walk.text
    assert walk.json()["params"]["device_id"] == str(other.id)
    walk = await send_command(client, admin, agent.agent_id, "mib_walk", {"ip": "192.168.10.50"})
    assert walk.json()["params"]["device_id"] == str(device.id)


async def test_set_config_result_updates_applied_version(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    _, admin, agent = await setup(client, factory)
    cmd = (await send_command(client, admin, agent.agent_id, "set_config")).json()
    assert cmd["params"] == {"config_version": 1}
    await report(agent, cmd["id"], "succeeded", result={"applied_config_version": 1})
    async with sessionmaker() as s:
        row = await s.get(Agent, uuid.UUID(agent.agent_id))
        assert row is not None
        assert row.applied_config_version == 1


async def test_every_command_is_audited(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    _, admin, agent = await setup(client, factory)
    cmd = (await send_command(client, admin, agent.agent_id, "scan_now")).json()
    await client.post(f"/api/v1/commands/{cmd['id']}/cancel", headers=auth(admin))
    async with sessionmaker() as s:
        actions = set(
            (await s.execute(select(AuditLog.action).where(AuditLog.entity_id == cmd["id"]))).scalars()
        )
    assert actions == {"command.scan_now", "command.cancel"}


async def test_uploads_of_logs_and_walks(
    client: httpx.AsyncClient, factory: Factory, test_settings: Settings
) -> None:
    _, admin, agent = await setup(client, factory)
    logs_cmd = (await send_command(client, admin, agent.agent_id, "get_logs", {"hours": 6})).json()
    walk_cmd = (await send_command(client, admin, agent.agent_id, "mib_walk", {"ip": "10.1.1.1"})).json()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("agent.log", '{"msg":"coletor iniciando"}\n')
    zipped = buf.getvalue()
    url = f"/api/agent/uploads/logs?command_id={logs_cmd['id']}"
    headers = {**agent.headers, "Content-Type": "application/zip"}
    assert (await client.post(url, content=b"nao e zip", headers=headers)).status_code == 400
    wrong = f"/api/agent/uploads/logs?command_id={walk_cmd['id']}"
    assert (await client.post(wrong, content=zipped, headers=headers)).status_code == 404
    up = await client.post(url, content=zipped, headers=headers)
    assert up.status_code == 200, up.text
    log_id = up.json()["id"]
    listed = (await client.get(f"/api/v1/agents/{agent.agent_id}/logs", headers=auth(admin))).json()
    assert [x["id"] for x in listed] == [log_id]
    assert listed[0]["hours"] == 6
    dl = await client.get(f"/api/v1/agent-logs/{log_id}/download", headers=auth(admin))
    assert dl.status_code == 200
    assert dl.content == zipped
    assert list(test_settings.storage_dir.rglob(f"{log_id}.zip")), "o arquivo fica no disco, fora do banco"

    snmprec = "1.3.6.1.2.1.1.1.0|4|Impressora\n1.3.6.1.2.1.1.2.0|6|1.3.6.1.4.1.1602.4.7\n# comentário\n"
    wurl = f"/api/agent/uploads/mib-walk?command_id={walk_cmd['id']}"
    wh = {**agent.headers, "Content-Type": "application/gzip"}
    assert (
        await client.post(wurl, content=gzip.compress(b"lixo sem pipes\n"), headers=wh)
    ).status_code == 400
    assert (await client.post(wurl, content=b"nao e gzip", headers=wh)).status_code == 400
    up = await client.post(wurl, content=gzip.compress(snmprec.encode()), headers=wh)
    assert up.status_code == 200, up.text
    walks = (
        await client.get("/api/v1/mib-walks", params={"agent_id": agent.agent_id}, headers=auth(admin))
    ).json()
    assert len(walks) == 1
    assert walks[0]["oid_count"] == 2
    assert walks[0]["ip"] == "10.1.1.1"
    dl = await client.get(f"/api/v1/mib-walks/{walks[0]['id']}/download", headers=auth(admin))
    assert gzip.decompress(dl.content).decode() == snmprec
    # Comando cancelado não aceita mais arquivo.
    await client.post(f"/api/v1/commands/{walk_cmd['id']}/cancel", headers=auth(admin))
    assert (await client.post(wurl, content=gzip.compress(snmprec.encode()), headers=wh)).status_code == 409


async def test_presence_sweep_and_offline(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    _, admin, agent = await setup(client, factory)
    await agent.heartbeat()
    aid = uuid.UUID(agent.agent_id)
    async with sessionmaker() as s:
        row = await s.get(Agent, aid)
        assert row is not None
        await presence_svc.connect(s, row, "gw-teste", "127.0.0.1")
        await s.commit()
        assert await presence_svc.connected_ids(s, [aid]) == {aid}
    info = (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=auth(admin))).json()
    assert info["ws_connected"] is True
    async with sessionmaker() as s:
        old = datetime.now(UTC) - timedelta(minutes=5)
        await s.execute(update(AgentPresence).where(AgentPresence.agent_id == aid).values(last_seen_at=old))
        await s.execute(update(Agent).where(Agent.id == aid).values(last_seen_at=old))
        await s.commit()
        assert await presence_svc.connected_ids(s, [aid]) == set()
        assert await presence_svc.sweep(s) == (1, 1)
        await s.commit()
        assert await presence_svc.sweep(s) == (0, 0)
    info = (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=auth(admin))).json()
    assert info["ws_connected"] is False
    assert info["state"] == "offline"
    await agent.heartbeat()
    assert (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=auth(admin))).json()[
        "state"
    ] == "online"


async def test_new_command_notifies_the_gateway(
    client: httpx.AsyncClient, factory: Factory, test_settings: Settings
) -> None:
    _, admin, agent = await setup(client, factory)
    conn = await asyncpg.connect(asyncpg_dsn(test_settings.database_url))
    got: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    try:
        await conn.add_listener("dm_command", lambda _c, _p, ch, payload: got.put_nowait((ch, payload)))
        await conn.add_listener("dm_agent_revoked", lambda _c, _p, ch, payload: got.put_nowait((ch, payload)))
        await send_command(client, admin, agent.agent_id, "reconnect")
        assert await asyncio.wait_for(got.get(), 5) == ("dm_command", agent.agent_id)
        await client.post(f"/api/v1/agents/{agent.agent_id}/revoke", headers=auth(admin))
        assert await asyncio.wait_for(got.get(), 5) == ("dm_agent_revoked", agent.agent_id)
    finally:
        await conn.close()


async def test_set_server_is_signed_with_the_agent_key(client: httpx.AsyncClient, factory: Factory) -> None:
    """ "Mudar endereço do servidor": só quem administra coletores; o endereço segue as regras do cadastro
    (https, ou http só com IP de rede privada) e vai assinado com a chave do coletor, que confere antes de
    trocar (agent/internal/agent/setserver.go)."""
    tenant, admin, agent = await setup(client, factory)
    _, tech_email = await factory.user(tenant.reseller_id, role="technician")
    tech = await login(client, tech_email)
    target = {"server_url": "https://monitor.exemplo.com.br/"}
    denied = await send_command(client, tech, agent.agent_id, "set_server", target)
    assert denied.status_code == 403, denied.text

    for bad in (
        "http://monitor.exemplo.com.br",  # http com nome: pode resolver para fora da rede
        "http://8.8.8.8:8000",  # http com IP público
        "http://127.0.0.1:8000",  # loopback: o coletor perderia o servidor
        "ftp://10.0.0.1",
        "https://monitor.exemplo.com.br/api",
    ):
        resp = await send_command(client, admin, agent.agent_id, "set_server", {"server_url": bad})
        assert resp.status_code == 400, (bad, resp.text)
    bad_ws = {
        "server_url": "https://monitor.exemplo.com.br",
        "ws_url": "ws://monitor.exemplo.com.br/ws/agent",
    }
    assert (await send_command(client, admin, agent.agent_id, "set_server", bad_ws)).status_code == 400

    lan = await send_command(
        client, admin, agent.agent_id, "set_server", {"server_url": "http://10.10.10.25:8000"}
    )
    assert lan.status_code == 201, lan.text
    resp = await send_command(client, admin, agent.agent_id, "set_server", target)
    assert resp.status_code == 201, resp.text
    cmd = resp.json()
    assert cmd["type_label"] == "Mudar endereço do servidor"
    delivered = {c["id"]: c for c in await pending(agent)}[cmd["id"]]
    params = delivered["params"]
    assert params["server_url"] == "https://monitor.exemplo.com.br"
    assert params["ws_url"] == ""
    assert abs(params["issued_at"] - int(datetime.now(UTC).timestamp())) < 60
    key = derive_agent_key(agent.secret)
    expected = set_server_signature(key, agent.agent_id, params["server_url"], "", params["issued_at"])
    assert params["signature"] == expected
    # Outra chave (outro coletor) não produz a mesma assinatura.
    other = set_server_signature(
        derive_agent_key(b"x" * 32), agent.agent_id, params["server_url"], "", params["issued_at"]
    )
    assert params["signature"] != other


def test_set_server_signature_vector() -> None:
    """Mesmo vetor de agent/internal/agent/setserver_test.go: Go e Python assinam igual."""
    sig = set_server_signature(
        bytes(range(32)),
        "agent-1",
        "https://monitor.exemplo.com.br",
        "wss://monitor.exemplo.com.br/ws/agent",
        1700000000,
    )
    assert sig == "47b35e12c44e8df0fb944350a185bcb06204ab1bf635ee609c068cfb2d0bacd1"
