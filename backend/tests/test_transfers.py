"""Equipamento que muda de cliente: a transferência espera aprovação (Descobertas > Transferências) com
alerta; aprovada, cada cliente vê só as leituras do período em que o equipamento esteve com ele."""

import dataclasses
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Alert, Device, DeviceAssignment, Site
from tests.agent_helpers import FakeAgent, enrolled_agent
from tests.conftest import Factory, Tenant, auth, login


async def _second_customer(
    factory: Factory, sessionmaker: async_sessionmaker[AsyncSession], t: Tenant
) -> Tenant:
    customer_id = await factory.customer(t.reseller_id, t.company_id, "Cliente Novo")
    async with sessionmaker() as s:
        site = Site(
            reseller_id=t.reseller_id, customer_id=customer_id, name="Matriz Nova", auto_activate_devices=True
        )
        s.add(site)
        await s.commit()
        return dataclasses.replace(t, customer_id=customer_id, site_id=site.id)


async def _read(agent: FakeAgent, serial: str, total: int, at: datetime) -> None:
    item = agent.reading(serial, {"total": total, "mono": total, "color": 0}, read_at=at)
    assert (await agent.send([item]))[0]["status"] == "accepted"


async def test_transfer_waits_for_approval_and_splits_history(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    t2 = await _second_customer(factory, sessionmaker, t)
    admin = await login(client, t.admin_email)
    old = await enrolled_agent(client, t, "PC antigo")
    new = await enrolled_agent(client, t2, "PC novo")
    now = datetime.now(UTC).replace(microsecond=0)
    base = now - timedelta(hours=5)
    await _read(old, "TRF-1", 1000, base)
    await _read(old, "TRF-1", 1500, base + timedelta(hours=1))

    # Aparece no outro cliente: não muda sozinho, abre alerta e entra em Transferências.
    await _read(new, "TRF-1", 1700, base + timedelta(hours=2))
    async with sessionmaker() as s:
        device = (await s.execute(select(Device).where(Device.serial == "TRF-1"))).scalar_one()
        assert device.customer_id == t.customer_id
        assert device.transfer_site_id == t2.site_id
        alert = (await s.execute(select(Alert).where(Alert.type == "device_transfer"))).scalar_one()
        assert "Cliente Novo" in alert.message
    pending = (await client.get("/api/v1/transfers", headers=auth(admin))).json()
    assert pending["total"] == 1
    row = pending["items"][0]
    assert (row["serial"], row["from_customer"], row["to_customer"]) == (
        "TRF-1",
        "Revenda A Cliente",
        "Cliente Novo",
    )

    # Aprovada: vai para o novo cliente desde que apareceu lá; o histórico separa as leituras.
    resp = await client.post(
        f"/api/v1/devices/{device.id}/transfer", json={"action": "approve"}, headers=auth(admin)
    )
    assert resp.status_code == 204, resp.text
    await _read(new, "TRF-1", 1800, base + timedelta(hours=3))
    period = {"date_from": (base - timedelta(days=1)).date().isoformat(), "date_to": now.date().isoformat()}
    old_prod = await client.get(
        "/api/v1/reports/production",
        params={**period, "customer_id": str(t.customer_id)},
        headers=auth(admin),
    )
    new_prod = await client.get(
        "/api/v1/reports/production",
        params={**period, "customer_id": str(t2.customer_id)},
        headers=auth(admin),
    )
    old_rows = {r["serial"]: r for r in old_prod.json()["rows"]}
    new_rows = {r["serial"]: r for r in new_prod.json()["rows"]}
    assert old_rows["TRF-1"]["total"] == 500  # 1000 → 1500 no cliente antigo
    assert old_rows["TRF-1"]["customer"] == "Revenda A Cliente"
    assert new_rows["TRF-1"]["total"] == 100  # 1700 → 1800 no novo (o transporte não conta)
    assert new_rows["TRF-1"]["customer"] == "Cliente Novo"
    async with sessionmaker() as s:
        history = (
            (
                await s.execute(
                    select(DeviceAssignment)
                    .where(DeviceAssignment.device_id == device.id)
                    .order_by(DeviceAssignment.start_at)
                )
            )
            .scalars()
            .all()
        )
        assert [h.customer_id for h in history] == [t.customer_id, t2.customer_id]
        assert history[0].end_at == history[1].start_at == base + timedelta(hours=2)
        alert = (await s.execute(select(Alert).where(Alert.type == "device_transfer"))).scalar_one()
        assert alert.state == "resolved"
    assert (await client.get("/api/v1/transfers", headers=auth(admin))).json()["total"] == 0


async def test_rejected_transfer_keeps_customer_and_ignores_site(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    t2 = await _second_customer(factory, sessionmaker, t)
    admin = await login(client, t.admin_email)
    old = await enrolled_agent(client, t, "PC antigo")
    new = await enrolled_agent(client, t2, "PC novo")
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=5)
    # Recusada: fica no cliente atual e aquele local passa a ser ignorado (sem novo alerta).
    await _read(old, "TRF-2", 50, base)
    await _read(new, "TRF-2", 60, base + timedelta(hours=1))
    async with sessionmaker() as s:
        d2 = (await s.execute(select(Device).where(Device.serial == "TRF-2"))).scalar_one()
    resp = await client.post(
        f"/api/v1/devices/{d2.id}/transfer", json={"action": "reject"}, headers=auth(admin)
    )
    assert resp.status_code == 204
    await _read(new, "TRF-2", 70, base + timedelta(hours=2))
    async with sessionmaker() as s:
        d2 = (await s.execute(select(Device).where(Device.serial == "TRF-2"))).scalar_one()
        assert (d2.customer_id, d2.transfer_site_id, d2.transfer_ignored_site_id) == (
            t.customer_id,
            None,
            t2.site_id,
        )
        open_transfers = (
            (await s.execute(select(Alert).where(Alert.type == "device_transfer", Alert.state != "resolved")))
            .scalars()
            .all()
        )
        assert open_transfers == []
    again = await client.post(
        f"/api/v1/devices/{d2.id}/transfer", json={"action": "approve"}, headers=auth(admin)
    )
    assert again.status_code == 409


async def test_move_within_same_customer_is_automatic(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    async with sessionmaker() as s:
        site = Site(
            reseller_id=t.reseller_id, customer_id=t.customer_id, name="Filial", auto_activate_devices=True
        )
        s.add(site)
        await s.commit()
    branch = dataclasses.replace(t, site_id=site.id)
    a = await enrolled_agent(client, t, "Matriz")
    b = await enrolled_agent(client, branch, "Filial")
    now = datetime.now(UTC)
    await _read(a, "MOV-1", 10, now - timedelta(hours=2))
    await _read(b, "MOV-1", 20, now - timedelta(hours=1))
    async with sessionmaker() as s:
        device = (await s.execute(select(Device).where(Device.serial == "MOV-1"))).scalar_one()
        assert (device.site_id, device.transfer_site_id) == (site.id, None)
        sites = (
            (
                await s.execute(
                    select(DeviceAssignment.site_id)
                    .where(DeviceAssignment.device_id == device.id)
                    .order_by(DeviceAssignment.start_at)
                )
            )
            .scalars()
            .all()
        )
        assert sites == [t.site_id, site.id]
