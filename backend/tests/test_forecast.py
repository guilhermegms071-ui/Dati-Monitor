"""Toner forecast (PROMPT 16.6) and retention (PROMPT 8)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import AgentHeartbeat, Reading, SupplyCurrent, SupplyReading
from app.services import forecast, retention
from app.services.forecast import Point
from tests.agent_helpers import enrolled_agent
from tests.alert_helpers import set_device, supply
from tests.conftest import Factory


def test_regression_window_and_replacement_cut() -> None:
    # 2% por dia, com ruído pequeno: ~30 dias restando a partir de 60%.
    pts = [Point(d, 80 - 2 * d + (0.3 if d % 2 else -0.3)) for d in range(11)]
    fc = forecast.compute(pts, 60.0, None)
    assert fc is not None
    assert fc.method == "regression"
    assert fc.days == pytest.approx(30, abs=1)
    assert fc.days_min < fc.days < fc.days_max
    assert fc.confidence > 0.9
    assert fc.pages_left is None

    # Cartucho trocado no meio: a regressão usa só os pontos depois da troca.
    replaced = [Point(0, 10), Point(1, 8), Point(2, 100), Point(3, 97), Point(4, 94), Point(5, 91)]
    assert forecast.since_last_replacement(replaced)[0] == Point(2, 100)
    fc = forecast.compute(replaced, 91.0, None)
    assert fc is not None
    assert fc.days == pytest.approx(91 / 3, rel=0.01)

    # Poucos pontos ou nível subindo: sem regressão; com páginas, cai no método direto.
    flat = [Point(0, 50), Point(0.5, 49)]
    assert forecast.compute(flat, 49.0, None) is None
    direct = forecast.compute(flat, 49.0, (100.0, 7.0))  # 1% em 100 páginas, 100 páginas em 7 dias
    assert direct is not None
    assert direct.method == "direct"
    assert direct.pages_left == 4900
    assert direct.days == pytest.approx(49 * 7, rel=0.01)
    assert direct.confidence == 0.3
    rising = [Point(0, 40), Point(1, 41), Point(2, 42)]
    assert forecast.compute(rising, 42.0, None) is None


async def test_forecast_job_uses_history_and_counters(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    now = datetime.now(UTC)
    items = []
    for day in range(10, -1, -1):
        at = now - timedelta(days=day)
        pct = 70 - 3 * (10 - day)  # 3% por dia: 70% → 40%
        items.append(agent.reading("SN-F", {"total": 10_000 + 150 * (10 - day)}, read_at=at))
        items.append(
            agent.item("supplies", serial="SN-F", read_at=at, supplies=[supply("1.1", "black", float(pct))])
        )
    assert {r["status"] for r in await agent.send(items)} == {"accepted"}
    device_id = await set_device(sessionmaker, "SN-F")
    async with sessionmaker() as s:
        assert await forecast.run(s, now) == 1
        await s.commit()
    async with sessionmaker() as s:
        sup = (
            await s.execute(select(SupplyCurrent).where(SupplyCurrent.device_id == device_id))
        ).scalar_one()
    assert sup.forecast_method == "regression"
    assert sup.days_to_empty == Decimal("13.3")  # 40% / 3% por dia
    assert sup.forecast_confidence is not None
    assert sup.forecast_confidence >= Decimal("0.99")
    # 30% em 1.500 páginas: 50 páginas por 1%, então 2.000 páginas para os 40% restantes.
    assert sup.pages_left == 2000
    assert sup.forecast_at is not None


async def test_retention_never_deletes_counter_readings(
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    old = datetime.now(UTC) - timedelta(days=500)
    await agent.send(
        [
            agent.reading("SN-R", {"total": 1}, read_at=old),
            agent.item("supplies", serial="SN-R", read_at=old, supplies=[supply("1.1", "black", 50)]),
            agent.item("supplies", serial="SN-R", supplies=[supply("1.1", "black", 49)]),
        ]
    )
    async with sessionmaker() as s:
        s.add(
            AgentHeartbeat(
                ts=old, agent_id=uuid.UUID(agent.agent_id), reseller_id=t.reseller_id, channel="ws"
            )
        )
        await s.commit()
    async with sessionmaker() as s:
        result = await retention.run(s, test_settings)
        await s.commit()
    assert (result.heartbeats, result.supply_readings) == (1, 1)
    async with sessionmaker() as s:
        assert (await s.execute(select(func.count()).select_from(Reading))).scalar_one() == 1
        assert (await s.execute(select(func.count()).select_from(SupplyReading))).scalar_one() == 1


async def test_dashboard_lists_only_confident_forecasts(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from sqlalchemy import update  # noqa: PLC0415

    from tests.alert_helpers import device_via_agent  # noqa: PLC0415
    from tests.conftest import auth, login  # noqa: PLC0415

    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    await device_via_agent(agent, "SN-D", supplies=[supply("1.1", "black", 20), supply("1.2", "cyan", 30)])
    device_id = await set_device(sessionmaker, "SN-D")

    async def set_forecast(key: str, days: str, confidence: str) -> None:
        async with sessionmaker() as s:
            await s.execute(
                update(SupplyCurrent)
                .where(SupplyCurrent.device_id == device_id, SupplyCurrent.supply_key == key)
                .values(days_to_empty=Decimal(days), forecast_confidence=Decimal(confidence))
            )
            await s.commit()

    await set_forecast("1.1", "5.0", "0.900")
    await set_forecast("1.2", "20.0", "0.200")  # incerta: fora do painel
    admin = await login(client, t.admin_email)
    dash = (await client.get("/api/v1/dashboard", headers=auth(admin))).json()
    assert [(s["serial"], s["color"]) for s in dash["ending_7_days"]] == [("SN-D", "black")]
    assert dash["ending_30_days_by_color"] == {"black": 1, "cyan": 0, "magenta": 0, "yellow": 0}
    await set_forecast("1.2", "20.0", "0.700")
    dash = (await client.get("/api/v1/dashboard", headers=auth(admin))).json()
    assert dash["ending_30_days_by_color"] == {"black": 1, "cyan": 1, "magenta": 0, "yellow": 0}
    supplies = (await client.get(f"/api/v1/devices/{device_id}/supplies", headers=auth(admin))).json()
    assert {s["supply_key"]: s["forecast_confidence"] for s in supplies} == {"1.1": "0.900", "1.2": "0.700"}
