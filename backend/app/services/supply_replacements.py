"""Supply replacement detection (PROMPT 16.3): a consumable whose level goes UP by at least the threshold
was replaced. Records levels, counters and the yield of the previous cartridge."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, DeviceEvent, Reading, Setting, SupplyCurrent, SupplyReplacement
from app.schemas import agent as proto

THRESHOLD_KEY = "supplies.replacement_threshold_points"
DEFAULT_THRESHOLD_POINTS = 20
PREMATURE_LEVEL = Decimal(20)  # trocado com mais de 20% ainda no cartucho anterior
CHROMATIC = frozenset({"cyan", "magenta", "yellow"})
PAGE_UNITS = frozenset({"impressions", "sheets"})


async def threshold_points(session: AsyncSession, reseller_id: object) -> int:
    value = (
        await session.execute(
            select(Setting.value).where(Setting.reseller_id == reseller_id, Setting.key == THRESHOLD_KEY)
        )
    ).scalar_one_or_none()
    if value and isinstance(value.get("value"), int | float) and value["value"] > 0:
        return int(value["value"])
    return DEFAULT_THRESHOLD_POINTS


async def _counters_at(session: AsyncSession, device_id: object, at: datetime) -> Reading | None:
    """Last counter reading at or before `at` (the counters are read in the same cycle as supplies)."""
    return (
        await session.execute(
            select(Reading)
            .where(Reading.device_id == device_id, Reading.read_at <= at)
            .order_by(Reading.read_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _yield_counter(color: str | None, before: Reading | None) -> str:
    """Black (or unknown) toner is used by every page; C/M/Y only by color pages."""
    if color in CHROMATIC and before is not None and before.color is not None:
        return "color"
    return "total"


async def detect(
    session: AsyncSession,
    device: Device,
    supply: proto.Supply,
    previous: SupplyCurrent | None,
    *,
    read_at: datetime,
    threshold: int,
) -> SupplyReplacement | None:
    if (
        previous is None
        or supply.class_ != "consumed"
        or supply.percent is None
        or previous.percent is None
        or read_at <= previous.read_at
    ):
        return None
    level_after = Decimal(str(round(supply.percent, 2)))
    if level_after - previous.percent < threshold:
        return None
    before = await _counters_at(session, device.id, previous.read_at)
    after = await _counters_at(session, device.id, read_at)
    counter = _yield_counter(supply.color or previous.color, before)
    last = (
        await session.execute(
            select(SupplyReplacement)
            .where(
                SupplyReplacement.device_id == device.id,
                SupplyReplacement.supply_key == supply.key,
                SupplyReplacement.replaced_at < read_at,
            )
            .order_by(SupplyReplacement.replaced_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    yield_pages: int | None = None
    if last is not None and before is not None:
        start = last.color_after if counter == "color" else last.total_after
        end = before.color if counter == "color" else before.total
        if start is not None and end is not None and end >= start:
            yield_pages = end - start
    nominal = supply.max_capacity if supply.unit in PAGE_UNITS and supply.max_capacity else None
    row = SupplyReplacement(
        reseller_id=device.reseller_id,
        device_id=device.id,
        supply_key=supply.key,
        description=supply.description or previous.description,
        supply_type=supply.type,
        color=supply.color or previous.color,
        replaced_at=read_at,
        previous_read_at=previous.read_at,
        level_before=previous.percent,
        level_after=level_after,
        total_before=before.total if before else None,
        mono_before=before.mono if before else None,
        color_before=before.color if before else None,
        total_after=after.total if after else None,
        mono_after=after.mono if after else None,
        color_after=after.color if after else None,
        yield_pages=yield_pages,
        yield_counter=counter,
        previous_replacement_id=last.id if last else None,
        nominal_capacity=nominal,
        premature=previous.percent > PREMATURE_LEVEL,
        cartridge_serial_before=previous.cartridge_serial,
        cartridge_serial_after=supply.cartridge_serial,
    )
    session.add(row)
    await session.flush()
    session.add(
        DeviceEvent(
            reseller_id=device.reseller_id,
            device_id=device.id,
            type="supply_replaced",
            data={
                "replacement_id": str(row.id),
                "supply_key": supply.key,
                "color": row.color,
                "level_before": float(row.level_before),
                "level_after": float(row.level_after),
                "yield_pages": yield_pages,
                "premature": row.premature,
            },
        )
    )
    return row
