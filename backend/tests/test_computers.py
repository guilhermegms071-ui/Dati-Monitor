"""Computadores e impressoras USB (PROMPT 11): a ingestão liga a impressora USB ao PC do coletor, a tela
lista PCs e impressoras (com ou sem contador por PJL) e a leitura manual segue as regras dos contadores."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog, Device, Reading, ReadingCounter
from app.services import park as park_svc
from tests.agent_helpers import FakeAgent, enrolled_agent
from tests.conftest import Factory, auth, login


def usb_item(
    agent: FakeAgent, kind: str, serial: str, read_at: datetime | None = None, **payload: Any
) -> dict[str, Any]:
    it = agent.item(kind, serial=serial, read_at=read_at, **payload)
    it["device"] = {
        "ip": "",
        "serial": serial,
        "source": "usb",
        "brand": "HP",
        "model": "HP LaserJet M15w",
        "hostname": "PC-RECEPCAO",
        "sys_descr": "HP LaserJet M15w (USB001)",
    }
    return it


def status(state: str = "ready") -> dict[str, Any]:
    return {"status": {"status": state, "error_bits": 0}}


async def test_usb_printers_and_computers(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    agent = await enrolled_agent(client, tenant, "PC da recepção")
    pjl_reading: dict[str, Any] = {
        "reading": {
            "counters": {"total": 12345},
            "counter_source": "pjl",
            "source": "usb",
            "status": "ready",
            "error_bits": 0,
        }
    }
    results = await agent.send(
        [
            usb_item(agent, "status", "VNC3K1", **status()),
            usb_item(agent, "reading", "VNC3K1", **pjl_reading),
            usb_item(agent, "status", "USB-ABCDEF123456", **status()),
        ]
    )
    assert {r["status"] for r in results} == {"accepted"}, results
    async with sessionmaker() as s:
        devices = {d.serial: d for d in (await s.execute(select(Device))).scalars()}
    hp = devices["VNC3K1"]
    assert (hp.source, str(hp.usb_agent_id), hp.ip, hp.brand) == ("usb", agent.agent_id, None, "HP")
    assert hp.last_total == 12345

    computers = (await client.get("/api/v1/computers", headers=auth(admin))).json()["items"]
    pc = next(c for c in computers if c["id"] == agent.agent_id)
    assert pc["usb_printers"] == 2
    assert pc["location"] == "Revenda A Cliente / Revenda A Local"
    printers = {
        p["serial"]: p
        for p in (
            await client.get(f"/api/v1/computers/{agent.agent_id}/usb-printers", headers=auth(admin))
        ).json()
    }
    assert printers["VNC3K1"]["counter_available"] is True
    assert printers["USB-ABCDEF123456"]["counter_available"] is False
    assert printers["USB-ABCDEF123456"]["name"] == "HP LaserJet M15w (USB001)"

    # Impressora USB que só manda status não é marcada desconectada enquanto o PC a vê.
    async with sessionmaker() as s:
        newly, _ = await park_svc.mark_disconnected(s, 6)
        await s.commit()
    assert newly == 0

    other = await factory.tenant("Outra empresa")
    stranger = await login(client, other.admin_email)
    resp = await client.get(f"/api/v1/computers/{agent.agent_id}/usb-printers", headers=auth(stranger))
    assert resp.status_code == 404


async def test_manual_reading_rules(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    agent = await enrolled_agent(client, tenant)
    await agent.send([usb_item(agent, "status", "USB-0000AAAA1111", **status())])
    async with sessionmaker() as s:
        device_id = (
            await s.execute(select(Device.id).where(Device.serial == "USB-0000AAAA1111"))
        ).scalar_one()
    url = f"/api/v1/devices/{device_id}/manual-readings"
    day1 = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    first = await client.post(
        url, json={"mono": 300, "color": 200, "read_at": day1, "note": "folha"}, headers=auth(admin)
    )
    assert first.status_code == 201, first.text
    assert first.json()["total"] == 500, "total = PB + cor quando não informado"

    lower = await client.post(url, json={"total": 400}, headers=auth(admin))
    assert lower.status_code == 400
    assert lower.json()["detail"]["code"] == "counter_lower"
    assert "menor que o da leitura" in lower.json()["detail"]["message"]
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    bad = await client.post(url, json={"total": 900, "read_at": future}, headers=auth(admin))
    assert bad.json()["detail"]["code"] == "future_read_at"
    assert (await client.post(url, json={}, headers=auth(admin))).json()["detail"]["code"] == "total_required"

    ok = await client.post(url, json={"total": 800, "mono": 500, "color": 300}, headers=auth(admin))
    assert ok.status_code == 201
    async with sessionmaker() as s:
        readings = list((await s.execute(select(Reading).where(Reading.device_id == device_id))).scalars())
        assert {r.source for r in readings} == {"manual"}
        assert all(r.user_id is not None for r in readings)
        lines = (
            (await s.execute(select(ReadingCounter).where(ReadingCounter.device_id == device_id)))
            .scalars()
            .all()
        )
        assert len(lines) >= 3
        device = await s.get(Device, device_id)
        assert device is not None
        assert device.last_total == 800
        audits = (
            (await s.execute(select(AuditLog.action).where(AuditLog.entity_id == str(device_id))))
            .scalars()
            .all()
        )
        assert "reading.manual" in audits

    # A leitura manual entra na produção (mesma regra dos relatórios).
    today = datetime.now(UTC).date()
    prod = (
        await client.get(
            "/api/v1/reports/production",
            params={"date_from": (today - timedelta(days=1)).isoformat(), "date_to": today.isoformat()},
            headers=auth(admin),
        )
    ).json()
    row = next(r for r in prod["rows"] if r["serial"] == "USB-0000AAAA1111")
    assert (row["total"], row["mono"], row["color"]) == (300, 200, 100)

    _, viewer_email = await factory.user(tenant.reseller_id, role="customer_viewer")
    viewer = await login(client, viewer_email)
    assert (await client.post(url, json={"total": 900}, headers=auth(viewer))).status_code == 403


async def test_read_now_of_usb_printer_goes_to_its_pc(client: httpx.AsyncClient, factory: Factory) -> None:
    """Só o PC em que a impressora USB está ligada a enxerga: o "Ler agora" vai para ele, mesmo que outro
    coletor do local seja o MASTER (que continua lendo as de rede)."""
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    master = await enrolled_agent(client, tenant, "PC do MASTER")
    await master.heartbeat()
    standby = await enrolled_agent(client, tenant, "PC da recepção")
    await standby.heartbeat()
    await standby.send([usb_item(standby, "status", "VNC3K1", **status())])
    await master.send([master.reading("KM-001", {"total": 100, "mono": 100})])
    rows = {
        r["serial"]: r["id"] for r in (await client.get("/api/v1/park", headers=auth(admin))).json()["items"]
    }
    resp = await client.post(
        "/api/v1/devices/bulk",
        json={"device_ids": [rows["VNC3K1"], rows["KM-001"]], "action": "read_now"},
        headers=auth(admin),
    )
    assert resp.status_code == 200, resp.text
    targets = {}
    for cid in resp.json()["commands"]:
        cmd = (await client.get(f"/api/v1/commands/{cid}", headers=auth(admin))).json()
        targets[cmd["agent_id"]] = [d["serial"] for d in cmd["params"]["devices"]]
    assert targets == {standby.agent_id: ["VNC3K1"], master.agent_id: ["KM-001"]}
