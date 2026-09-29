"""Portal read access to devices, readings, supplies and events (always tenant-scoped).
The full park screen (filters, bulk actions, exports) comes in Phase 4."""

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import Device, DeviceEvent, Reading, SupplyCurrent
from app.services.pagination import Direction, PageResult, SortOption, paginate

DEVICE_SORTS = {
    "serial": SortOption(Device.serial, "str"),
    "ip": SortOption(func.coalesce(Device.ip, ""), "str"),
    "created_at": SortOption(Device.created_at, "datetime"),
    "last_read_at": SortOption(func.coalesce(Device.last_read_at, datetime(1970, 1, 1)), "datetime"),
}


async def list_devices(
    session: AsyncSession,
    p: Principal,
    *,
    site_id: uuid.UUID | None,
    customer_id: uuid.UUID | None,
    q: str | None,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> PageResult[Device]:
    p.require("devices.read")
    stmt = select(Device).where(
        Device.deleted_at.is_(None),
        Device.discovery_state == "approved",  # pendentes e descartados ficam em Descobertas (16.1)
        reseller_scope(p, Device.reseller_id),
        customer_scope(p, Device.customer_id),
    )
    if site_id:
        stmt = stmt.where(Device.site_id == site_id)
    if customer_id:
        stmt = stmt.where(Device.customer_id == customer_id)
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(Device.serial.ilike(like), Device.ip.ilike(like), Device.model.ilike(like)))
    return await paginate(
        session,
        stmt,
        id_column=Device.id,
        sort_options=DEVICE_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )


async def get_device(session: AsyncSession, p: Principal, device_id: uuid.UUID) -> Device:
    p.require("devices.read")
    d = await session.get(Device, device_id)
    if d is None or d.deleted_at is not None or not p.can_access_customer(d.reseller_id, d.customer_id):
        raise not_found("Equipamento")
    return d


READING_SORTS = {"read_at": SortOption(Reading.read_at, "datetime")}


async def list_readings(
    session: AsyncSession,
    p: Principal,
    device_id: uuid.UUID,
    *,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    cursor: str | None,
) -> PageResult[Reading]:
    d = await get_device(session, p, device_id)
    stmt = select(Reading).where(Reading.device_id == d.id)
    if date_from:
        stmt = stmt.where(Reading.read_at >= date_from)
    if date_to:
        stmt = stmt.where(Reading.read_at < date_to)
    return await paginate(
        session,
        stmt,
        id_column=Reading.id,
        sort_options=READING_SORTS,
        sort="read_at",
        direction="desc",
        limit=limit,
        cursor=cursor,
    )


async def list_supplies(session: AsyncSession, p: Principal, device_id: uuid.UUID) -> list[SupplyCurrent]:
    d = await get_device(session, p, device_id)
    rows = await session.execute(
        select(SupplyCurrent).where(SupplyCurrent.device_id == d.id).order_by(SupplyCurrent.supply_key)
    )
    return list(rows.scalars())


EVENT_SORTS = {"created_at": SortOption(DeviceEvent.created_at, "datetime")}


async def list_events(
    session: AsyncSession, p: Principal, device_id: uuid.UUID, *, limit: int, cursor: str | None
) -> PageResult[DeviceEvent]:
    d = await get_device(session, p, device_id)
    stmt = select(DeviceEvent).where(DeviceEvent.device_id == d.id)
    return await paginate(
        session,
        stmt,
        id_column=DeviceEvent.id,
        sort_options=EVENT_SORTS,
        sort="created_at",
        direction="desc",
        limit=limit,
        cursor=cursor,
    )
