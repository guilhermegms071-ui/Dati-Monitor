"""Helpers of the alert/notification tests: devices through the real ingestion and direct state tweaks."""

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Agent, Alert, Device
from app.services import alert_engine
from tests.agent_helpers import FakeAgent


async def device_via_agent(
    agent: FakeAgent, serial: str, *, ip: str = "10.0.0.5", supplies: list[dict[str, Any]] | None = None
) -> None:
    items = [agent.reading(serial, {"total": 1000, "mono": 800, "color": 200}, ip=ip)]
    if supplies:
        items.append(agent.item("supplies", serial=serial, ip=ip, supplies=supplies))
    results = await agent.send(items)
    assert {r["status"] for r in results} == {"accepted"}, results


def supply(key: str, color: str, percent: float, *, type_: str = "toner") -> dict[str, Any]:
    return {
        "key": key,
        "description": f"Toner {color}",
        "type": type_,
        "class": "consumed",
        "color": color,
        "level": int(percent),
        "max_capacity": 100,
        "percent": percent,
        "level_state": "ok",
        "unit": "percent",
    }


async def set_agent(maker: async_sessionmaker[AsyncSession], agent_id: str, **values: Any) -> None:
    async with maker() as s:
        await s.execute(update(Agent).where(Agent.id == uuid.UUID(agent_id)).values(**values))
        await s.commit()


async def set_device(maker: async_sessionmaker[AsyncSession], serial: str, **values: Any) -> uuid.UUID:
    async with maker() as s:
        device_id = (
            await s.execute(
                update(Device).where(Device.serial == serial).values(**values).returning(Device.id)
            )
        ).scalar_one()
        await s.commit()
    return device_id


async def evaluate(
    maker: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> alert_engine.EvalResult:
    async with maker() as s:
        result = await alert_engine.evaluate(s, now or datetime.now(UTC))
        await s.commit()
    return result


async def open_alerts(maker: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    """dedup_key → state of every non-resolved alert."""
    async with maker() as s:
        rows = await s.execute(select(Alert.dedup_key, Alert.state).where(Alert.state != "resolved"))
        return dict(rows.tuples().all())


def mock_transport(responses: list[int], seen: list[httpx.Request]) -> httpx.MockTransport:
    """Answers with the given status codes in order (the last one repeats) and records each request."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        code = responses[min(len(seen) - 1, len(responses) - 1)]
        return httpx.Response(code, json={"ok": code < 400})

    return httpx.MockTransport(handler)
