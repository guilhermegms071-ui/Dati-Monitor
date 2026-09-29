"""Retention (worker, daily — PROMPT 8): heartbeats, supply readings and the notification log are pruned
after the configured days. Counter readings are NEVER deleted (R6)."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import AgentHeartbeat, Notification, SupplyReading

logger = logging.getLogger(__name__)


@dataclass
class RetentionResult:
    heartbeats: int
    supply_readings: int
    notifications: int


async def run(session: AsyncSession, settings: Settings, now: datetime | None = None) -> RetentionResult:
    now = now or datetime.now(UTC)
    hb = await session.execute(
        delete(AgentHeartbeat).where(
            AgentHeartbeat.ts < now - timedelta(days=settings.retention_heartbeat_days)
        )
    )
    sup = await session.execute(
        delete(SupplyReading).where(
            SupplyReading.read_at < now - timedelta(days=settings.retention_supply_readings_days)
        )
    )
    notif = await session.execute(
        delete(Notification).where(
            Notification.created_at < now - timedelta(days=settings.retention_notifications_days),
            Notification.status != "pending",
        )
    )

    def count(r: object) -> int:
        return cast("CursorResult[Any]", r).rowcount or 0

    return RetentionResult(count(hb), count(sup), count(notif))
