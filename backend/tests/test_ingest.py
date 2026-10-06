"""Reading ingestion (PROMPT 4.3/4.4/4.6/4.8/6.5)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Alert,
    Device,
    DeviceEvent,
    Reading,
    ReadingDiscard,
    ReadingIdempotency,
    SupplyCurrent,
    SupplyReading,
)
from tests.agent_helpers import enrolled_agent
from tests.conftest import Factory, auth, login

pytestmark = pytest.mark.usefixtures("clean_db")

KONICA = {
    "total": 217031,
    "mono": 100150,
    "color": 116881,
    "copy_mono": 40150,
    "print_mono": 60000,
    "copy_color": 16881,
    "print_color": 100000,
    "duplex": 5000,
    "scan": 7777,
}


async def _events(sm: async_sessionmaker[AsyncSession]) -> list[str]:
    async with sm() as s:
        return [
            e.type for e in (await s.execute(select(DeviceEvent).order_by(DeviceEvent.created_at))).scalars()
        ]


async def test_batch_with_every_kind_is_idempotent(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    items = [
        agent.reading("A797019500624", KONICA, ip="192.168.1.50"),
        agent.item(
            "supplies",
            serial="A797019500624",
            ip="192.168.1.50",
            supplies=[
                {
                    "key": "1.1",
                    "description": "Toner (Black)",
                    "type": "toner",
                    "class": "consumed",
                    "color": "black",
                    "level": 25,
                    "max_capacity": 100,
                    "percent": 25.0,
                    "level_state": "ok",
                    "unit": "percent",
                },
                {
                    "key": "1.5",
                    "description": "Waste",
                    "type": "wasteToner",
                    "class": "receptacle",
                    "color": None,
                    "level": -3,
                    "max_capacity": 100,
                    "percent": None,
                    "level_state": "some_remaining",
                },
            ],
        ),
        agent.item(
            "status",
            serial="A797019500624",
            ip="192.168.1.50",
            status={
                "status": "error",
                "error_bits": 48,
                "reasons": ["doorOpen", "jammed"],
                "panel_text": "Atolamento",
                "alerts": [{"severity": 3, "code": 8, "description": "Atolamento"}],
            },
        ),
        agent.item(
            "event",
            serial="SEMRESPOSTA",
            ip="192.168.1.99",
            event={"type": "read_failed", "data": {"attempts": 3}},
        ),
    ]
    results = await agent.send(items)
    assert [r["status"] for r in results] == ["accepted"] * 4
    again = await agent.send(items, gz=False)
    assert [r["status"] for r in again] == ["duplicate"] * 4
    async with sessionmaker() as s:
        dev = (await s.execute(select(Device).where(Device.serial == "A797019500624"))).scalar_one()
        assert (dev.ip, dev.brand, dev.model, dev.last_total, dev.last_mono, dev.last_color) == (
            "192.168.1.50",
            "Konica Minolta",
            "bizhub C287",
            217031,
            100150,
            116881,
        )
        assert dev.last_status == "error"
        assert dev.last_error_reasons == ["doorOpen", "jammed"]
        assert dev.is_color is True
        assert dev.customer_id == t.customer_id
        reading = (await s.execute(select(Reading))).scalar_one()
        assert (
            reading.total,
            reading.mono,
            reading.color,
            reading.copy_mono,
            reading.print_color,
            reading.scan,
        ) == (217031, 100150, 116881, 40150, 100000, 7777)
        assert reading.extra["counters"]["duplex"] == 5000
        assert reading.flags == []
        assert reading.agent_id is not None
        sup = {x.supply_key: x for x in (await s.execute(select(SupplyCurrent))).scalars()}
        assert sup["1.1"].percent == 25
        assert sup["1.5"].level_state == "some_remaining"
        assert len((await s.execute(select(SupplyReading))).scalars().all()) == 2
        offline = (await s.execute(select(Device).where(Device.serial == "SEMRESPOSTA"))).scalar_one()
        assert offline.last_status == "offline"
        keys = (await s.execute(select(ReadingIdempotency.kind))).scalars().all()
        assert sorted(keys) == ["event", "reading", "status", "supplies"]
    assert await _events(sessionmaker) == ["discovered", "discovered", "read_failed"]


async def test_regression_is_stored_flagged_and_alerted(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    t0 = datetime.now(UTC) - timedelta(hours=2)
    await agent.send([agent.reading("SIMREG08", {"total": 500000}, read_at=t0)])
    res = await agent.send([agent.reading("SIMREG08", {"total": 400000}, read_at=t0 + timedelta(hours=1))])
    assert res[0]["status"] == "accepted"  # nunca rejeitada
    async with sessionmaker() as s:
        readings = (await s.execute(select(Reading).order_by(Reading.read_at))).scalars().all()
        assert [r.total for r in readings] == [500000, 400000]
        assert readings[1].flags == ["counter_regression"]
        alert = (await s.execute(select(Alert))).scalar_one()
        assert alert.type == "counter_regression"
        assert alert.data["total"] == {"before": 500000, "after": 400000}
    assert "counter_regression" in await _events(sessionmaker)


async def test_jump_and_sum_mismatch_flags(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    t0 = datetime.now(UTC) - timedelta(days=3)
    await agent.send([agent.reading("J1", {"total": 1000, "mono": 1000, "color": 0}, read_at=t0)])
    await agent.send(
        [agent.reading("J1", {"total": 90000, "mono": 90000, "color": 0}, read_at=t0 + timedelta(hours=12))]
    )
    await agent.send([agent.reading("S1", {"total": 1000, "mono": 900, "color": 50}, read_at=t0)])
    await agent.send([agent.reading("S2", {"total": 1000, "mono": 990, "color": 5}, read_at=t0, tolerance=2)])
    async with sessionmaker() as s:
        by_serial: dict[str, list[Reading]] = {}
        for r, serial in (
            await s.execute(
                select(Reading, Device.serial)
                .join(Device, Device.id == Reading.device_id)
                .order_by(Reading.read_at)
            )
        ).all():
            by_serial.setdefault(serial, []).append(r)
        assert by_serial["J1"][-1].flags == ["suspicious_jump"]
        assert by_serial["S1"][0].flags == ["sum_mismatch"]
        assert by_serial["S2"][0].flags == []  # 0,5% está dentro da tolerância
        types = sorted(a.type for a in (await s.execute(select(Alert))).scalars())
        assert types == ["sum_mismatch", "suspicious_jump"]


async def test_future_read_at_is_clamped_and_old_queue_does_not_override_current(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    future = datetime.now(UTC) + timedelta(days=400)
    await agent.send([agent.reading("F1", {"total": 10}, read_at=future)])
    old = datetime.now(UTC) - timedelta(days=20)  # fila antiga chegando depois
    await agent.send([agent.reading("F1", {"total": 5}, read_at=old)])
    async with sessionmaker() as s:
        rows = (await s.execute(select(Reading).order_by(Reading.read_at))).scalars().all()
        assert rows[1].flags == ["future_read_at"]
        assert rows[1].read_at < future - timedelta(days=300)
        assert rows[1].extra["original_read_at"].startswith(str(future.year))
        dev = (await s.execute(select(Device))).scalar_one()
        assert dev.last_total == 10


async def test_identity_events_ip_change_replace_and_move(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    await agent.send([agent.reading("OLD1", {"total": 10}, ip="10.0.0.5")])
    await agent.send([agent.reading("NEW1", {"total": 20}, ip="10.0.0.5")])  # outro serial no mesmo IP
    await agent.send([agent.reading("NEW1", {"total": 21}, ip="10.0.0.9")])  # mesmo serial, IP novo
    events = await _events(sessionmaker)
    assert events == ["discovered", "discovered", "replaced", "ip_changed"]
    async with sessionmaker() as s:
        old = (await s.execute(select(Device).where(Device.serial == "OLD1"))).scalar_one()
        assert old.ip is None
    # Outro local da mesma revenda passa a ver o equipamento: mudança de local.
    admin = auth(await login(client, t.admin_email))
    site2 = (
        await client.post(
            "/api/v1/sites", json={"customer_id": str(t.customer_id), "name": "Filial"}, headers=admin
        )
    ).json()
    from tests.agent_helpers import FakeAgent, create_agent  # noqa: PLC0415

    created = await create_agent(client, (await login(client, t.admin_email)), site2["id"], "Coletor Filial")
    enroll = await client.post(
        "/api/agent/enroll", json={"v": 1, "code": created["enrollment"]["code"], "kind": "windows"}
    )
    import base64  # noqa: PLC0415

    agent2 = FakeAgent(client, enroll.json()["agent_id"], base64.b64decode(enroll.json()["secret"]))
    await agent2.authenticate()
    # A leitura pode ser descartada pela anti-duplicidade (outro coletor leu há pouco), mas a
    # identidade (local e IP novos) é atualizada de qualquer forma.
    later = datetime.now(UTC) + timedelta(minutes=2)
    res = await agent2.send([agent2.reading("NEW1", {"total": 30}, ip="10.1.0.9", read_at=later)])
    assert res[0]["status"] in ("accepted", "discarded")
    async with sessionmaker() as s:
        dev = (await s.execute(select(Device).where(Device.serial == "NEW1"))).scalar_one()
        assert str(dev.site_id) == site2["id"]
        assert dev.ip == "10.1.0.9"
    assert (await _events(sessionmaker))[4:6] == ["moved_site", "ip_changed"]


async def test_cluster_anti_duplication_discards_and_records(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    a1 = await enrolled_agent(client, t, "PC 1")
    a2 = await enrolled_agent(client, t, "PC 2")
    t0 = datetime.now(UTC) - timedelta(hours=3)
    await a1.send([a1.reading("DUP1", {"total": 100}, read_at=t0)])
    res = await a2.send([a2.reading("DUP1", {"total": 101}, read_at=t0 + timedelta(minutes=10))])
    assert res[0]["status"] == "discarded"  # 10 min < metade do intervalo (60 min)
    again = await a2.send([a2.reading("DUP1", {"total": 150}, read_at=t0 + timedelta(minutes=45))])
    assert again[0]["status"] == "accepted"
    async with sessionmaker() as s:
        assert len((await s.execute(select(Reading))).scalars().all()) == 2
        discard = (await s.execute(select(ReadingDiscard))).scalar_one()
        assert discard.reason == "duplicate_other_agent"
        assert discard.payload["counters"] == {"total": 101}
        idem = (
            await s.execute(select(ReadingIdempotency).where(ReadingIdempotency.result == "discarded"))
        ).scalar_one()
        assert idem.reading_id is None


async def test_rejections_and_bad_bodies(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    no_serial = agent.reading("", {"total": 1})
    foreign_key = agent.reading("X1", {"total": 1})
    foreign_key["key"] = "outro-agente:1"
    empty = agent.item("status", serial="X2")
    results = await agent.send([no_serial, foreign_key, empty, agent.reading("OK", {"total": 1})])
    assert [r["status"] for r in results] == ["rejected", "rejected", "rejected", "accepted"]
    assert "série" in results[0]["reason"]
    h = {**agent.headers, "Content-Type": "application/json"}
    bad_json = await client.post("/api/agent/readings", content=b"{nope", headers=h)
    assert bad_json.status_code == 400
    bad_gzip = await client.post(
        "/api/agent/readings", content=b"not gzip", headers={**h, "Content-Encoding": "gzip"}
    )
    assert bad_gzip.json()["detail"]["code"] == "invalid_gzip"
    no_token = await client.post("/api/agent/readings", content=b"{}")
    assert no_token.status_code == 401
    too_many = {"v": 1, "items": [agent.reading(f"S{i}", {"total": 1}) for i in range(501)]}
    over = await client.post("/api/agent/readings", json=too_many, headers=agent.headers)
    assert over.status_code == 400


async def test_devices_read_api_and_scope(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    now = datetime.now(UTC)
    await agent.send(
        [
            agent.reading("D1", {"total": 10}, read_at=now - timedelta(hours=2)),
            agent.reading("D1", {"total": 20}, read_at=now - timedelta(hours=1)),
        ]
    )
    admin = auth(await login(client, t.admin_email))
    devices = (await client.get("/api/v1/devices", headers=admin)).json()["items"]
    assert [d["serial"] for d in devices] == ["D1"]
    dev_id = devices[0]["id"]
    readings = (await client.get(f"/api/v1/devices/{dev_id}/readings", headers=admin)).json()["items"]
    assert [r["total"] for r in readings] == [20, 10]
    assert (await client.get(f"/api/v1/devices/{dev_id}/supplies", headers=admin)).json() == []
    events = (await client.get(f"/api/v1/devices/{dev_id}/events", headers=admin)).json()["items"]
    assert [e["type"] for e in events] == ["discovered"]
    b = await factory.tenant("Revenda B")
    hb = auth(await login(client, b.admin_email))
    assert (await client.get(f"/api/v1/devices/{dev_id}", headers=hb)).status_code == 404
    assert (await client.get("/api/v1/devices", headers=hb)).json()["items"] == []


async def test_invalid_item_is_rejected_alone_and_the_rest_of_the_batch_goes_in(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Um item fora do formato não derruba o lote: antes era 400 no lote inteiro e o coletor reenviava o
    mesmo lote para sempre (fila travada, coletor "Degradado")."""
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    good = agent.reading("A797019500624", KONICA, ip="192.168.1.50")
    bad = agent.reading("SERIALRUIM01", KONICA, ip="192.168.1.51")
    bad["device"]["port"] = 0  # fora de 1..65535
    no_kind = agent.item("telepatia", serial="X1")
    results = await agent.send([good, bad, no_kind])
    by_key = {r["key"]: r for r in results}
    assert by_key[good["key"]]["status"] == "accepted"
    assert by_key[bad["key"]]["status"] == "rejected"
    assert "device.port" in by_key[bad["key"]]["reason"]
    assert by_key[no_kind["key"]]["status"] == "rejected"
    assert "kind" in by_key[no_kind["key"]]["reason"]
    async with sessionmaker() as s:
        serials = set((await s.execute(select(Device.serial))).scalars())
    assert serials == {"A797019500624"}


async def test_malformed_batch_is_still_refused_whole(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    headers = {**agent.headers, "Content-Type": "application/json"}
    for body in (b"{nao e json", b'{"v": 1}', b'{"v": 2, "items": []}'):
        resp = await client.post("/api/agent/readings", content=body, headers=headers)
        assert resp.status_code == 400, (body, resp.text)
        assert resp.json()["detail"]["code"] == "invalid_batch"
