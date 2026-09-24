"""Monthly partition maintenance for readings, supply_readings and agent_heartbeats."""

import logging
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PARTITIONED_TABLES

logger = logging.getLogger(__name__)


def month_starts(today: date, months_back: int, months_ahead: int) -> list[date]:
    first = today.replace(day=1)
    out: list[date] = []
    for offset in range(-months_back, months_ahead + 1):
        idx = first.year * 12 + first.month - 1 + offset
        out.append(date(idx // 12, idx % 12 + 1, 1))
    return out


async def ensure_partitions(
    session: AsyncSession, *, months_back: int = 1, months_ahead: int = 3, today: date | None = None
) -> list[str]:
    """Creates missing monthly partitions, moving rows from the default partition. Returns created names."""
    created: list[str] = []
    for month in month_starts(today or datetime.now(UTC).date(), months_back, months_ahead):
        for table, column in PARTITIONED_TABLES:
            made = (
                await session.execute(
                    text("SELECT dm_ensure_month_partition(:t, :c, :m)"),
                    {"t": table, "c": column, "m": month},
                )
            ).scalar_one()
            if made:
                name = f"{table}_{month:%Y_%m}"
                created.append(name)
                logger.info("partição criada: %s", name)
    return created


async def default_partition_rows(session: AsyncSession) -> dict[str, int]:
    """Rows sitting in each default partition (should be zero in steady state)."""
    counts: dict[str, int] = {}
    for table, _ in PARTITIONED_TABLES:
        counts[table] = (await session.execute(text(f"SELECT count(*) FROM {table}_default"))).scalar_one()  # noqa: S608 - nomes fixos
    return counts
