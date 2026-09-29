"""Toner forecast (worker, hourly — PROMPT 8 and 16.6).

Linear regression of the level over the last 30 days (only since the last replacement) gives the
consumption per day; the standard error of the slope gives the optimistic/pessimistic window. When the
regression is not usable, the direct method uses the level consumed per page and the pages printed per
day. The confidence (0 to 1) combines fit, number of points and time span; the portal never shows a
low-confidence forecast as certain.
"""

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reading, SupplyCurrent, SupplyReading
from app.services.alert_engine import TONER_TYPES

WINDOW_DAYS = 30
REPLACEMENT_JUMP = 10.0  # subida que separa cartuchos (troca) no histórico
MIN_POINTS = 3
MIN_SPAN_DAYS = 1.0
MAX_DAYS = 3650.0
Z95 = 1.96


@dataclass(frozen=True)
class Point:
    days: float  # dias desde o primeiro ponto
    percent: float


@dataclass(frozen=True)
class Forecast:
    days: float
    days_min: float
    days_max: float
    pages_left: int | None
    method: str  # regression | direct
    confidence: float


def since_last_replacement(points: list[Point]) -> list[Point]:
    """Keeps only the points after the last level increase (a new cartridge)."""
    start = 0
    for i in range(1, len(points)):
        if points[i].percent - points[i - 1].percent >= REPLACEMENT_JUMP:
            start = i
    return points[start:]


def regression(points: list[Point]) -> tuple[float, float, float] | None:
    """Slope (%/day), its standard error and R². None when it cannot be computed."""
    n = len(points)
    if n < MIN_POINTS:
        return None
    mx = sum(p.days for p in points) / n
    my = sum(p.percent for p in points) / n
    sxx = sum((p.days - mx) ** 2 for p in points)
    if sxx == 0:
        return None
    sxy = sum((p.days - mx) * (p.percent - my) for p in points)
    slope = sxy / sxx
    intercept = my - slope * mx
    sse = sum((p.percent - (intercept + slope * p.days)) ** 2 for p in points)
    syy = sum((p.percent - my) ** 2 for p in points)
    r2 = 1 - sse / syy if syy > 0 else 0.0
    se = math.sqrt(sse / (n - 2)) / math.sqrt(sxx) if n > 2 else float("inf")  # noqa: PLR2004
    return slope, se, max(0.0, min(1.0, r2))


def compute(points: list[Point], current: float, pages: tuple[float, float] | None) -> Forecast | None:
    """`pages` = (pages printed during the span of `points`, span in days) for the direct method."""
    pts = since_last_replacement(points)
    span = pts[-1].days - pts[0].days if pts else 0.0
    pct_per_page = None
    if pages and pages[0] > 0 and len(pts) >= 2:  # noqa: PLR2004
        consumed = pts[0].percent - pts[-1].percent
        if consumed > 0:
            pct_per_page = consumed / pages[0]
    pages_left = int(current / pct_per_page) if pct_per_page else None
    reg = regression(pts) if span >= MIN_SPAN_DAYS else None
    if reg is not None and reg[0] < 0:
        slope, se, r2 = reg
        rate = -slope
        rate_hi = rate + Z95 * se
        rate_lo = max(rate - Z95 * se, rate * 0.1)
        confidence = r2 * min(1.0, len(pts) / 10) * min(1.0, span / 7)
        return Forecast(
            days=min(current / rate, MAX_DAYS),
            days_min=min(current / rate_hi, MAX_DAYS),
            days_max=min(current / rate_lo, MAX_DAYS),
            pages_left=pages_left,
            method="regression",
            confidence=round(confidence, 3),
        )
    if pct_per_page and pages and pages[1] > 0:
        pages_per_day = pages[0] / pages[1]
        days = (current / pct_per_page) / pages_per_day
        return Forecast(
            days=min(days, MAX_DAYS),
            days_min=min(days * 0.7, MAX_DAYS),
            days_max=min(days * 1.3, MAX_DAYS),
            pages_left=pages_left,
            method="direct",
            confidence=round(0.6 * min(1.0, pages[1] / 14), 3),
        )
    return None


async def _pages(
    session: AsyncSession, device_id: uuid.UUID, color: str | None, start: datetime, end: datetime
) -> tuple[float, float] | None:
    rows = (
        (
            await session.execute(
                select(Reading.read_at, Reading.total, Reading.color)
                .where(Reading.device_id == device_id, Reading.read_at >= start, Reading.read_at <= end)
                .order_by(Reading.read_at)
            )
        )
        .tuples()
        .all()
    )
    chromatic = color in ("cyan", "magenta", "yellow")
    values = [(t, c if chromatic else tot) for t, tot, c in rows if (c if chromatic else tot) is not None]
    if len(values) < 2:  # noqa: PLR2004
        return None
    (t0, v0), (t1, v1) = values[0], values[-1]
    span = (t1 - t0).total_seconds() / 86400
    if v1 is None or v0 is None or v1 < v0 or span <= 0:
        return None
    return float(v1 - v0), span


async def run(session: AsyncSession, now: datetime | None = None) -> int:
    """Recomputes the forecast of every consumable toner. Returns how many got a forecast."""
    now = now or datetime.now(UTC)
    start = now - timedelta(days=WINDOW_DAYS)
    supplies = (
        (
            await session.execute(
                select(SupplyCurrent).where(
                    SupplyCurrent.supply_class == "consumed", SupplyCurrent.supply_type.in_(TONER_TYPES)
                )
            )
        )
        .scalars()
        .all()
    )
    done = 0
    for sup in supplies:
        rows = (
            (
                await session.execute(
                    select(SupplyReading.read_at, SupplyReading.percent)
                    .where(
                        SupplyReading.device_id == sup.device_id,
                        SupplyReading.supply_key == sup.supply_key,
                        SupplyReading.read_at >= start,
                        SupplyReading.percent.is_not(None),
                    )
                    .order_by(SupplyReading.read_at)
                )
            )
            .tuples()
            .all()
        )
        fc: Forecast | None = None
        if rows and sup.percent is not None:
            t0 = rows[0][0]
            points = [Point((t - t0).total_seconds() / 86400, float(p)) for t, p in rows if p is not None]
            kept = since_last_replacement(points)
            first = t0 + timedelta(days=kept[0].days) if kept else t0
            pages = await _pages(session, sup.device_id, sup.color, first, now)
            fc = compute(points, float(sup.percent), pages)
        sup.forecast_at = now
        if fc is None:
            sup.days_to_empty = sup.days_to_empty_min = sup.days_to_empty_max = None
            sup.pages_left = sup.forecast_method = sup.forecast_confidence = None
            continue
        sup.days_to_empty = Decimal(f"{fc.days:.1f}")
        sup.days_to_empty_min = Decimal(f"{fc.days_min:.1f}")
        sup.days_to_empty_max = Decimal(f"{fc.days_max:.1f}")
        sup.pages_left = fc.pages_left
        sup.forecast_method = fc.method
        sup.forecast_confidence = Decimal(f"{fc.confidence:.3f}")
        done += 1
    await session.flush()
    return done
