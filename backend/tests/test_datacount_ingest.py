"""Ingestion added by the Datacount audit: counters as rows (16.2), supply replacements (16.3),
printer alerts from prtAlertTable (16.4) and daily attributes / sector from sysLocation (16.7, 16.8)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Device,
    DeviceAttributeSnapshot,
    DeviceEvent,
    PrinterAlert,
    ReadingCounter,
    SupplyReplacement,
)
from app.schemas.agent import Alert
from app.services.counter_lines import Line, resolve_lines
from app.services.printer_alerts import alert_key, classify
from tests.agent_helpers import FakeAgent, enrolled_agent
from tests.conftest import Factory, auth, login

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _supply(key: str, percent: float | None, **kw: Any) -> dict[str, Any]:
    return {
        "key": key,
        "description": kw.pop("description", f"Toner {key}"),
        "type": kw.pop("type", "toner"),
        "class": kw.pop("class_", "consumed"),
        "color": kw.pop("color", "black"),
        "level": None if percent is None else int(percent),
        "max_capacity": kw.pop("max_capacity", 100),
        "percent": percent,
        "level_state": "ok" if percent is not None else "unknown",
        "unit": kw.pop("unit", "percent"),
        **kw,
    }


async def _device(maker: async_sessionmaker[AsyncSession], serial: str) -> Device:
    async with maker() as s:
        return (await s.execute(select(Device).where(Device.serial == serial))).scalar_one()


# ----------------------------------------------------------------------------- reading_counters (16.2)


async def test_every_mapped_counter_becomes_a_row_and_rows_are_immutable(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    counters = {
        "total": 217031,
        "mono": 100150,
        "color": 116881,
        "copy_mono": 40000,
        "print_mono": 60150,
        "copy_color": 16881,
        "print_color": 100000,
        "duplex": 5000,
        "scan": 777,
        "mono_small": 123,  # sem significado inequívoco: fica só no extra
        "a3_color_prints": 42,  # contador novo mapeado pelo perfil
    }
    item = agent.reading("SN-LINES", counters)
    item["reading"]["counter_lines"] = {
        "a3_color_prints": {"kind": "print", "color_mode": "full_color", "size": "a3"}
    }
    assert (await agent.send([item]))[0]["status"] == "accepted"
    async with sessionmaker() as s:
        rows = (await s.execute(select(ReadingCounter))).scalars().all()
        got = {(r.kind, r.color_mode, r.size): (r.name, r.value) for r in rows}
    assert got == {
        ("total", "any", "any"): ("total", 217031),
        ("total", "mono", "any"): ("mono", 100150),
        ("total", "full_color", "any"): ("color", 116881),
        ("copy", "mono", "any"): ("copy_mono", 40000),
        ("print", "mono", "any"): ("print_mono", 60150),
        ("copy", "full_color", "any"): ("copy_color", 16881),
        ("print", "full_color", "any"): ("print_color", 100000),
        ("duplex", "any", "any"): ("duplex", 5000),
        ("scan", "any", "any"): ("scan", 777),
        ("print", "full_color", "a3"): ("a3_color_prints", 42),
    }
    # Somente inserção, como readings (R6).
    async with sessionmaker() as s:
        with pytest.raises(DBAPIError, match="somente inserção"):
            await s.execute(text("UPDATE reading_counters SET value = 0"))
        await s.rollback()
        with pytest.raises(DBAPIError, match="somente inserção"):
            await s.execute(text("DELETE FROM reading_counters"))


def test_profile_mapping_wins_and_collisions_are_reported() -> None:
    rows, conflicts = resolve_lines(
        {"total": 10, "grand_total": 11, "mono": 5},
        {"grand_total": Line("total", "any", "any")},
    )
    # O mapeamento explícito do perfil vence o padrão; o outro contador da mesma linha é reportado.
    assert [(n, v) for n, _, v in rows] == [("grand_total", 11), ("mono", 5)]
    assert conflicts == ["total"]


# ----------------------------------------------------------------------------- troca de toner (16.3)


async def test_level_going_up_records_replacement_with_yield(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    serial = "SN-TONER"

    async def cycle(at: datetime, total: int, color: int, levels: dict[str, float]) -> None:
        items = [
            agent.reading(serial, {"total": total, "mono": total - color, "color": color}, read_at=at),
            agent.item(
                "supplies",
                serial=serial,
                read_at=at,
                supplies=[
                    _supply("1.1", levels["k"], color="black", cartridge_serial=f"K-{int(levels['k'])}"),
                    _supply("1.2", levels["c"], color="cyan", unit="impressions", max_capacity=6000),
                    _supply("1.9", levels["w"], color=None, type="wasteToner", class_="receptacle"),
                ],
            ),
        ]
        assert {r["status"] for r in await agent.send(items)} == {"accepted"}

    await cycle(T0, 10_000, 3_000, {"k": 30, "c": 50, "w": 40})
    await cycle(T0 + timedelta(days=1), 10_500, 3_100, {"k": 8, "c": 45, "w": 60})
    # Preto trocado (8% → 100%). Ciano sobe só 10 pontos: abaixo do limiar de 20, não é troca.
    await cycle(T0 + timedelta(days=2), 10_900, 3_150, {"k": 100, "c": 55, "w": 10})
    await cycle(T0 + timedelta(days=5), 13_900, 3_900, {"k": 70, "c": 2, "w": 20})
    # Ciano trocado (2% → 99%) e preto trocado de novo com 70% ainda: prematuro.
    await cycle(T0 + timedelta(days=6), 14_500, 4_000, {"k": 97, "c": 99, "w": 30})

    async with sessionmaker() as s:
        reps = (
            (
                await s.execute(
                    select(SupplyReplacement).order_by(SupplyReplacement.replaced_at, SupplyReplacement.color)
                )
            )
            .scalars()
            .all()
        )
        events = (
            (await s.execute(select(DeviceEvent).where(DeviceEvent.type == "supply_replaced")))
            .scalars()
            .all()
        )
    assert [(r.color, r.replaced_at) for r in reps] == [
        ("black", T0 + timedelta(days=2)),
        ("black", T0 + timedelta(days=6)),
        ("cyan", T0 + timedelta(days=6)),
    ]
    first, second, cyan = reps
    assert (first.level_before, first.level_after, first.premature) == (Decimal(8), Decimal(100), False)
    assert (first.total_before, first.total_after) == (10_500, 10_900)
    assert (first.cartridge_serial_before, first.cartridge_serial_after) == ("K-8", "K-100")
    assert first.yield_pages is None  # sem troca anterior conhecida: rendimento desconhecido
    assert first.yield_counter == "total"
    # 2ª troca do preto: páginas com o cartucho anterior = total antes desta menos o total depois da 1ª.
    assert (second.premature, second.previous_replacement_id) == (True, first.id)
    assert second.yield_pages == 13_900 - 10_900
    # Ciano usa o contador de cor e registra a capacidade nominal em páginas.
    assert (cyan.yield_counter, cyan.nominal_capacity, cyan.premature) == ("color", 6000, False)
    assert len(events) == 3


# ----------------------------------------------------------------------------- alertas da impressora (16.4)


def test_prt_alert_classification() -> None:
    def cls(code: int, group: int | None = None, training: int | None = None) -> str:
        return classify(Alert(code=code, group=group, training_level=training))

    assert cls(8, 13) == "jam"
    assert cls(1104, 11) == "consumable"  # markerTonerAlmostEmpty
    assert cls(13, 11) == "consumable"  # subunitEmpty num suprimento
    assert cls(13, 8) == "other"  # subunitEmpty numa bandeja (papel): não é consumível do marcador
    assert cls(1112, 10) == "parts"  # markerOpcLifeOver
    assert cls(10, 11) == "parts"  # subunitLifeAlmostOver
    assert cls(1002, 10) == "service_call"  # markerFuserOverTemperature
    assert cls(30, 5) == "service_call"  # subunitUnrecoverableFailure
    assert cls(2, 5, training=5) == "service_call"  # fieldService
    assert cls(3, 6) == "other"  # coverOpen


async def test_status_alerts_are_recorded_once_with_counters_and_cleared(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    serial = "SN-ALERTS"
    await agent.send([agent.reading(serial, {"total": 5000, "mono": 4000, "color": 1000}, read_at=T0)])
    jam = {
        "index": 7,
        "severity": 3,
        "training_level": 4,
        "group": 13,
        "group_index": 1,
        "location": 3,
        "code": 8,
        "description": "Atolamento na bandeja 1",
        "time": 1000,
    }
    toner = {
        "index": 8,
        "severity": 4,
        "training_level": 3,
        "group": 11,
        "group_index": 1,
        "location": 0,
        "code": 1104,
        "description": "Toner preto baixo",
        "time": 1100,
    }

    async def status(at: datetime, alerts: list[dict[str, Any]]) -> None:
        st = {"status": "error", "error_bits": 0, "reasons": [], "alerts": alerts}
        assert (await agent.send([agent.item("status", serial=serial, read_at=at, status=st)]))[0][
            "status"
        ] == "accepted"

    await status(T0 + timedelta(minutes=10), [jam, toner])
    await status(T0 + timedelta(minutes=20), [jam, toner])  # mesmos alertas: nada novo
    await status(T0 + timedelta(minutes=30), [toner])  # o atolamento sumiu da tabela
    # Novo atolamento no mesmo índice (a impressora reusa o índice): prtAlertTime diferente = alerta novo.
    await status(T0 + timedelta(minutes=40), [toner, {**jam, "time": 5000}])
    # Status antigo chegando depois (fila): ignorado.
    await status(T0 + timedelta(minutes=5), [])

    async with sessionmaker() as s:
        rows = (await s.execute(select(PrinterAlert).order_by(PrinterAlert.first_seen_at))).scalars().all()
    assert [(r.category, r.code, r.first_seen_at, r.cleared_at) for r in rows] == [
        ("jam", 8, T0 + timedelta(minutes=10), T0 + timedelta(minutes=30)),
        ("consumable", 1104, T0 + timedelta(minutes=10), None),
        ("jam", 8, T0 + timedelta(minutes=40), None),
    ]
    first = rows[0]
    assert (first.total_at, first.mono_at, first.color_at) == (5000, 4000, 1000)
    assert (first.severity, first.training_level, first.group, first.location) == (3, 4, 13, 3)
    assert first.description == "Atolamento na bandeja 1"
    assert rows[1].last_seen_at == T0 + timedelta(minutes=40)
    assert first.alert_key != alert_key(Alert.model_validate({**jam, "time": 5000}))


# ----------------------------------------------------------------------------- atributos e setor (16.7/16.8)


async def test_attributes_snapshots_and_sector_following_sys_location(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent: FakeAgent = await enrolled_agent(client, t)
    admin = await login(client, t.admin_email)
    serial = "SN-ATTR"
    attrs = {
        "firmware": ["G00-R1", "Controller 2.1"],
        "memory_bytes": 2 * 1024**3,
        "storage": [{"description": "HDD", "size_bytes": 250 * 10**9, "used_bytes": 10**9}],
        "mac": "00:11:22:33:44:55",
        "uptime_seconds": 3600,
        "subsystems": [{"name": "printer", "description": "bizhub", "status": "running"}],
        "panel_text": "Pronta",
        "sys_location": "Financeiro - 2º andar",
        "parts": [{"name": "drum_k", "part": "drum", "color": "black", "unit": "percent", "value": 64}],
    }

    async def send_attrs(at: datetime, **changes: Any) -> None:
        item = agent.item("attributes", serial=serial, read_at=at, attributes={**attrs, **changes})
        assert (await agent.send([item]))[0]["status"] == "accepted"

    await send_attrs(T0)
    await send_attrs(T0 + timedelta(days=1), uptime_seconds=90000)  # só o uptime mudou: sem histórico novo
    await send_attrs(T0 + timedelta(days=2), firmware=["G00-R2", "Controller 2.1"])
    async with sessionmaker() as s:
        snaps = (
            (await s.execute(select(DeviceAttributeSnapshot).order_by(DeviceAttributeSnapshot.read_at)))
            .scalars()
            .all()
        )
    assert [sn.read_at for sn in snaps] == [T0, T0 + timedelta(days=2)]
    device = await _device(sessionmaker, serial)
    assert device.attributes["firmware"] == ["G00-R2", "Controller 2.1"]
    assert device.attributes["parts"][0]["value"] == 64
    assert (device.sys_location, device.sector, device.sector_from_snmp) == (
        "Financeiro - 2º andar",
        "Financeiro - 2º andar",
        True,
    )
    detail = (await client.get(f"/api/v1/devices/{device.id}", headers=auth(admin))).json()
    assert detail["attributes"]["storage"][0]["description"] == "HDD"
    assert detail["attributes_at"] is not None

    # Setor digitado no portal não é mais sobrescrito pelo sysLocation…
    resp = await client.patch(f"/api/v1/devices/{device.id}", json={"sector": "Compras"}, headers=auth(admin))
    assert resp.status_code == 200, resp.text
    await send_attrs(
        T0 + timedelta(days=3), sys_location="Almoxarifado", firmware=["G00-R2", "Controller 2.1"]
    )
    device = await _device(sessionmaker, serial)
    assert (device.sector, device.sys_location, device.sector_from_snmp) == ("Compras", "Almoxarifado", False)
    # …e apagá-lo volta a seguir a impressora.
    await client.patch(f"/api/v1/devices/{device.id}", json={"sector": ""}, headers=auth(admin))
    device = await _device(sessionmaker, serial)
    assert (device.sector, device.sector_from_snmp) == ("Almoxarifado", True)
    # Atributos antigos chegando depois (fila) não voltam o estado atual.
    await send_attrs(T0 - timedelta(days=1), firmware=["velho"])
    assert (await _device(sessionmaker, serial)).attributes["firmware"] == ["G00-R2", "Controller 2.1"]
