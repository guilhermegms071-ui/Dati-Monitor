"""Counter math shared by the reports, the dashboard and the ERP API (PROMPT 6.5 / 15 item 10-11).

Valid reading: the reading with its latest manual adjustment applied (field by field), excluding readings
the operator classified as `read_error` and readings flagged `counter_regression` that nobody classified as
`valid` or `board_replacement`. A regressed counter therefore never enters production.

Production of a period: sum, per device, of the positive increases between consecutive valid readings
(the first pair uses the last valid reading before the period). A drop between two valid readings (board
replaced, counter reset) counts as zero, and production goes on from the new value.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DISPLAY_TZ_NAME = "America/Sao_Paulo"
# Quanto olhar para trás atrás da leitura-base do período (equipamento que ficou parado).
BASELINE_LOOKBACK = timedelta(days=62)


def valid_readings_cte(scope_sql: str) -> str:
    """CTE `eff` with the valid readings of the devices in scope between :lo_base and :hi."""
    return f"""
        adj AS (
            SELECT DISTINCT ON (a.reading_id) a.reading_id, a.total, a.mono, a.color
            FROM reading_adjustments a
            WHERE a.read_at >= :lo_base AND a.read_at < :hi
            ORDER BY a.reading_id, a.created_at DESC
        ),
        eff AS (
            SELECT r.device_id, r.id AS reading_id, r.read_at,
                   coalesce(adj.total, r.total, r.mono + r.color) AS total,
                   coalesce(adj.mono, r.mono) AS mono,
                   coalesce(adj.color, r.color) AS color,
                   adj.reading_id IS NOT NULL AS adjusted
            FROM readings r
            JOIN devices d ON d.id = r.device_id
            LEFT JOIN adj ON adj.reading_id = r.id
            LEFT JOIN reading_reviews rv ON rv.reading_id = r.id AND rv.read_at = r.read_at
            WHERE r.read_at >= :lo_base AND r.read_at < :hi AND {scope_sql}
              AND coalesce(rv.classification, '') <> 'read_error'
              AND (NOT (r.flags ? 'counter_regression')
                   OR rv.classification IN ('valid', 'board_replacement'))
        )"""  # noqa: S608 - fragmentos fixos; valores por parâmetro


def pairs_cte() -> str:
    """CTE `pairs`: increase of each valid reading over the previous valid one (never negative)."""
    return """
        pairs AS (
            SELECT device_id, read_at,
                   greatest(total - lag(total) OVER w, 0) AS d_total,
                   greatest(mono - lag(mono) OVER w, 0) AS d_mono,
                   greatest(color - lag(color) OVER w, 0) AS d_color
            FROM eff
            WINDOW w AS (PARTITION BY device_id ORDER BY read_at)
        )"""


@dataclass(frozen=True)
class Production:
    device_id: uuid.UUID
    total: int
    mono: int
    color: int
    readings: int


async def production_by_device(
    session: AsyncSession, scope_sql: str, params: dict[str, Any], lo: datetime, hi: datetime
) -> dict[uuid.UUID, Production]:
    sql = text(
        f"""
        WITH {valid_readings_cte(scope_sql)}, {pairs_cte()}
        SELECT device_id,
               coalesce(sum(d_total), 0)::bigint, coalesce(sum(d_mono), 0)::bigint,
               coalesce(sum(d_color), 0)::bigint, count(*)
        FROM pairs WHERE read_at >= :lo
        GROUP BY device_id
        """  # noqa: S608
    )
    rows = await session.execute(sql, {**params, "lo": lo, "hi": hi, "lo_base": lo - BASELINE_LOOKBACK})
    return {r[0]: Production(r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in rows.all()}


@dataclass(frozen=True)
class DayProduction:
    day: date
    total: int
    mono: int
    color: int
    devices: int  # equipamentos com leitura válida no dia


async def production_by_day(
    session: AsyncSession, scope_sql: str, params: dict[str, Any], lo: datetime, hi: datetime
) -> dict[date, DayProduction]:
    sql = text(
        f"""
        WITH {valid_readings_cte(scope_sql)}, {pairs_cte()}
        SELECT (read_at AT TIME ZONE :tz)::date AS day,
               coalesce(sum(d_total), 0)::bigint, coalesce(sum(d_mono), 0)::bigint,
               coalesce(sum(d_color), 0)::bigint, count(DISTINCT device_id)
        FROM pairs WHERE read_at >= :lo
        GROUP BY day
        """  # noqa: S608
    )
    rows = await session.execute(
        sql, {**params, "lo": lo, "hi": hi, "lo_base": lo - BASELINE_LOOKBACK, "tz": DISPLAY_TZ_NAME}
    )
    return {r[0]: DayProduction(r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in rows.all()}


@dataclass(frozen=True)
class CutoffReading:
    device_id: uuid.UUID
    reading_id: uuid.UUID
    read_at: datetime
    total: int | None
    mono: int | None
    color: int | None


async def cutoff(
    session: AsyncSession, scope_sql: str, params: dict[str, Any], before: datetime
) -> dict[uuid.UUID, CutoffReading]:
    """Latest valid reading of each device strictly before `before` (the cutoff instant)."""
    sql = text(
        f"""
        SELECT d.id, c.id, c.read_at,
               coalesce(adj.total, c.total, c.mono + c.color), coalesce(adj.mono, c.mono),
               coalesce(adj.color, c.color)
        FROM devices d
        JOIN LATERAL (
            SELECT r.id, r.read_at, r.total, r.mono, r.color
            FROM readings r
            LEFT JOIN reading_reviews rv ON rv.reading_id = r.id AND rv.read_at = r.read_at
            WHERE r.device_id = d.id AND r.read_at < :before
              AND coalesce(rv.classification, '') <> 'read_error'
              AND (NOT (r.flags ? 'counter_regression')
                   OR rv.classification IN ('valid', 'board_replacement'))
            ORDER BY r.read_at DESC
            LIMIT 1
        ) c ON true
        LEFT JOIN LATERAL (
            SELECT a.total, a.mono, a.color FROM reading_adjustments a
            WHERE a.device_id = d.id AND a.reading_id = c.id
            ORDER BY a.created_at DESC LIMIT 1
        ) adj ON true
        WHERE {scope_sql}
        """  # noqa: S608
    )
    rows = await session.execute(sql, {**params, "before": before})
    return {r[0]: CutoffReading(r[0], r[1], r[2], _int(r[3]), _int(r[4]), _int(r[5])) for r in rows.all()}


def _int(v: Any) -> int | None:
    return None if v is None else int(v)
