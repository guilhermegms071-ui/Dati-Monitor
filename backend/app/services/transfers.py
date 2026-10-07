"""Transferência de equipamento entre clientes (Descobertas > Transferências): quando o equipamento aparece
num local de outro cliente, a mudança espera a decisão. Aprovar move o equipamento desde o momento em que
ele apareceu lá (o histórico em device_assignments separa as leituras de cada cliente); recusar mantém no
cliente atual e ignora aquele local daqui em diante."""

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import bad_request, conflict, not_found
from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import Customer, Device, DeviceEvent, Site
from app.schemas.discoveries import TransferDecisionIn, TransferOut, TransferPage
from app.services import assignments, audit
from app.services.alerts import resolve_alerts
from app.services.pagination import Direction, SortOption, paginate

ToSite = aliased(Site)
ToCustomer = aliased(Customer)
TRANSFER_SORTS = {"detected_at": SortOption(Device.transfer_detected_at, "datetime")}


def _joined(stmt: Select[Any], p: Principal) -> Select[Any]:
    return (
        stmt.join(Customer, Customer.id == Device.customer_id)
        .join(Site, Site.id == Device.site_id)
        .join(ToSite, ToSite.id == Device.transfer_site_id)
        .join(ToCustomer, ToCustomer.id == ToSite.customer_id)
        .where(
            Device.deleted_at.is_(None),
            Device.transfer_site_id.is_not(None),
            reseller_scope(p, Device.reseller_id),
            customer_scope(p, Device.customer_id),
        )
    )


async def list_pending(
    session: AsyncSession, p: Principal, *, direction: Direction, limit: int, cursor: str | None
) -> TransferPage:
    p.require("devices.read")
    stmt = _joined(select(Device), p)
    page = await paginate(
        session,
        stmt,
        id_column=Device.id,
        sort_options=TRANSFER_SORTS,
        sort="detected_at",
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    names_stmt = _joined(select(Device.id, Customer.name, Site.name, ToCustomer.name, ToSite.name), p)
    names: dict[uuid.UUID, tuple[str, str, str, str]] = {
        row[0]: (row[1], row[2], row[3], row[4])
        for row in (
            await session.execute(names_stmt.where(Device.id.in_([d.id for d in page.items])))
        ).tuples()
    }
    items = []
    for d in page.items:
        from_customer, from_site, to_customer, to_site = names[d.id]
        if d.transfer_detected_at is None:
            raise RuntimeError(f"transferência sem data: {d.id}")
        items.append(
            TransferOut(
                device_id=d.id,
                serial=d.serial,
                model=d.model,
                from_customer=from_customer,
                from_site=from_site,
                to_customer=to_customer,
                to_site=to_site,
                detected_at=d.transfer_detected_at,
            )
        )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    return TransferPage(items=items, next_cursor=page.next_cursor, total=total)


async def decide(session: AsyncSession, p: Principal, device_id: uuid.UUID, data: TransferDecisionIn) -> None:
    p.require("devices.update")
    device = (
        await session.execute(
            select(Device)
            .where(
                Device.id == device_id,
                Device.deleted_at.is_(None),
                reseller_scope(p, Device.reseller_id),
                customer_scope(p, Device.customer_id),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if device is None:
        raise not_found("Equipamento")
    if device.transfer_site_id is None or device.transfer_detected_at is None:
        raise conflict("no_transfer", "Este equipamento não tem transferência pendente")
    site = await session.get(Site, device.transfer_site_id)
    if site is None or site.deleted_at is not None:
        raise bad_request("invalid_site", "O local de destino não existe mais; recuse a transferência")
    if data.action == "approve" and not p.can_access_customer(site.reseller_id, site.customer_id):
        raise bad_request("invalid_site", "Você não tem acesso ao cliente de destino")
    detail = {
        "from_site_id": str(device.site_id),
        "to_site_id": str(site.id),
        "detected_at": device.transfer_detected_at.isoformat(),
    }
    if data.action == "approve":
        at = device.transfer_detected_at
        await assignments.move(session, device, site, at)
        event = "transfer_approved"
    else:
        device.transfer_ignored_site_id = site.id
        event = "transfer_rejected"
    device.transfer_site_id = None
    device.transfer_detected_at = None
    session.add(
        DeviceEvent(
            reseller_id=device.reseller_id, device_id=device.id, type=event, data=detail, user_id=p.user_id
        )
    )
    await resolve_alerts(session, f"device_transfer:{device.id}", user_id=p.user_id)
    await audit.record(
        session,
        p,
        action=f"transfer_{data.action}",
        entity="device",
        entity_id=device.id,
        reseller_id=device.reseller_id,
        after=detail,
    )
