"""Equipamentos > Descobertas (PROMPT 16.1): pending until activated; discarded stops being read."""

import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog, Device, DeviceEvent, Reading
from tests.agent_helpers import FakeAgent, enrolled_agent
from tests.conftest import Factory, auth, login


async def _park_serials(client: httpx.AsyncClient, token: str) -> set[str]:
    resp = await client.get("/api/v1/park", headers=auth(token))
    assert resp.status_code == 200, resp.text
    return {r["serial"] for r in resp.json()["items"]}


async def _discoveries(client: httpx.AsyncClient, token: str, state: str = "pending") -> dict[str, Any]:
    resp = await client.get("/api/v1/discoveries", params={"state": state}, headers=auth(token))
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _decide(client: httpx.AsyncClient, token: str, action: str, ids: list[str]) -> httpx.Response:
    return await client.post(
        "/api/v1/discoveries/decide", json={"action": action, "device_ids": ids}, headers=auth(token)
    )


async def test_new_device_waits_in_discoveries_until_activated(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant(auto_activate=False)
    agent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)
    results = await agent.send(
        [
            agent.reading("SN-NEW-1", {"total": 1000, "mono": 600, "color": 400}),
            agent.reading("SN-NEW-2", {"total": 50}, ip="10.0.0.6"),
        ]
    )
    assert [r["status"] for r in results] == ["accepted", "accepted"]

    # Pendente: leituras gravadas (nada se perde), mas fora do parque e do painel.
    async with sessionmaker() as s:
        states = dict((await s.execute(select(Device.serial, Device.discovery_state))).tuples().all())
        assert states == {"SN-NEW-1": "pending", "SN-NEW-2": "pending"}
        assert len((await s.execute(select(Reading))).scalars().all()) == 2
    assert await _park_serials(client, admin) == set()
    dash = (await client.get("/api/v1/dashboard", headers=auth(admin))).json()
    assert dash["cards"]["devices_monitored"] == 0
    listed = await _discoveries(client, admin)
    assert (listed["total"], {r["serial"] for r in listed["items"]}) == (2, {"SN-NEW-1", "SN-NEW-2"})
    counts = (await client.get("/api/v1/discoveries/counts", headers=auth(admin))).json()
    assert counts == {"pending": 2, "discarded": 0}

    ids = [r["id"] for r in listed["items"]]
    resp = await _decide(client, admin, "approve", ids)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"changed": 2, "skipped": []}
    assert await _park_serials(client, admin) == {"SN-NEW-1", "SN-NEW-2"}
    # Ativar de novo um já ativo: pulado com o motivo, sem erro.
    again = (await _decide(client, admin, "approve", ids[:1])).json()
    assert again["changed"] == 0
    assert again["skipped"][0]["reason"] == "estado atual: approved"
    async with sessionmaker() as s:
        events = (await s.execute(select(DeviceEvent.type).where(DeviceEvent.type == "approved"))).scalars()
        assert len(list(events)) == 2
        audit = (
            await s.execute(select(AuditLog.action).where(AuditLog.action == "discovery_approve"))
        ).scalars()
        assert len(list(audit)) == 2


async def test_discarded_device_goes_to_ignored_serials_and_items_are_dropped(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant(auto_activate=False)
    agent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)
    await agent.heartbeat()
    await agent.send([agent.reading("SN-TRASH", {"total": 10})])
    device_id = (await _discoveries(client, admin))["items"][0]["id"]
    before = (await agent.heartbeat())["config_version"]

    assert (await _decide(client, admin, "discard", [device_id])).json()["changed"] == 1
    assert (await agent.heartbeat())["config_version"] == before + 1  # o coletor busca a config nova
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert cfg["ignored_serials"] == ["SN-TRASH"]
    # O que ainda estava na fila do coletor é respondido como descartado (e sai da fila dele).
    late = await agent.send([agent.reading("SN-TRASH", {"total": 11})])
    assert (late[0]["status"], late[0]["reason"]) == ("discarded", "device_discarded")
    assert (await _discoveries(client, admin, "discarded"))["total"] == 1
    assert (await _discoveries(client, admin))["total"] == 0

    # Restaurar volta para pendente e tira o serial da lista de ignorados.
    assert (await _decide(client, admin, "restore", [device_id])).json()["changed"] == 1
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert cfg["ignored_serials"] == []
    assert (await agent.send([agent.reading("SN-TRASH", {"total": 12})]))[0]["status"] == "accepted"
    assert (await _discoveries(client, admin))["total"] == 1


async def test_site_auto_activation_and_decision_permissions(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant(auto_activate=False)
    agent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)
    resp = await client.patch(
        f"/api/v1/sites/{t.site_id}", json={"auto_activate_devices": True}, headers=auth(admin)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["auto_activate_devices"] is True
    await agent.send([agent.reading("SN-AUTO", {"total": 5})])
    async with sessionmaker() as s:
        device = (await s.execute(select(Device).where(Device.serial == "SN-AUTO"))).scalar_one()
        assert device.discovery_state == "approved"
        assert device.discovery_decided_at is not None

    await client.patch(
        f"/api/v1/sites/{t.site_id}", json={"auto_activate_devices": False}, headers=auth(admin)
    )
    await agent.send([agent.reading("SN-PEND", {"total": 5}, ip="10.0.0.9")])
    pending_id = (await _discoveries(client, admin))["items"][0]["id"]
    # Técnico altera equipamentos, mas não inclui nem exclui (matriz padrão): não ativa nem descarta.
    _, tech_email = await factory.user(t.reseller_id, role="technician")
    tech = await login(client, tech_email)
    assert (await _decide(client, tech, "approve", [pending_id])).status_code == 403
    assert (await _decide(client, tech, "discard", [pending_id])).status_code == 403
    missing = await _decide(client, admin, "approve", [str(uuid.uuid4())])
    assert missing.json()["skipped"][0]["reason"] == "não encontrado"
    # Outra revenda não enxerga (nem decide) os pendentes desta.
    other = await factory.tenant("Revenda B", auto_activate=False)
    other_admin = await login(client, other.admin_email)
    assert (await _discoveries(client, other_admin))["total"] == 0
    assert (await _decide(client, other_admin, "approve", [pending_id])).json()["changed"] == 0


async def test_agent_config_lists_discarded_serials_of_the_reseller_only(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant(auto_activate=False)
    a: FakeAgent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)
    await a.send([a.reading("SN-X", {"total": 1})])
    await _decide(client, admin, "discard", [(await _discoveries(client, admin))["items"][0]["id"]])
    other = await factory.tenant("Revenda B")
    b = await enrolled_agent(client, other)
    cfg = (await client.get("/api/agent/config", headers=b.headers)).json()
    assert cfg["ignored_serials"] == []
    # O mesmo serial na outra revenda é outro equipamento: entra normalmente.
    assert (await b.send([b.reading("SN-X", {"total": 1})]))[0]["status"] == "accepted"
