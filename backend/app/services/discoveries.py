"""Equipamentos > Descobertas (PROMPT 16.1): new devices wait as `pending` until someone activates or
discards them. Discarded serials go to the agents' configuration (`ignored_serials`) so they stop reading."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request
from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import Customer, Device, DeviceEvent, Site
from app.schemas.discoveries import DecisionIn, DecisionOut, DiscoveryCounts
from app.services import audit
from app.services.agents import bump_site_config
from app.services.pagination import Direction, PageResult, SortOption, paginate

# Ação → (estado de destino, permissão da matriz, tipo de evento).
ACTIONS: dict[str, tuple[str, str, str]] = {
    "approve": ("approved", "devices.create", "approved"),
    "discard": ("discarded", "devices.delete", "discarded"),
    "restore": ("pending", "devices.update", "restored"),
}
# De onde cada ação pode partir: ativar/descartar pendentes; restaurar descartados.
ALLOWED_FROM: dict[str, frozenset[str]] = {
    "approve": frozenset({"pending", "discarded"}),
    "discard": frozenset({"pending"}),
    "restore": frozenset({"discarded"}),
}

DISCOVERY_SORTS = {
    "first_seen_at": SortOption(Device.first_seen_at, "datetime"),
    "serial": SortOption(Device.serial, "str"),
    "model": SortOption(func.coalesce(Device.model, ""), "str"),
    "ip": SortOption(func.coalesce(Device.ip, ""), "str"),
    "customer": SortOption(Customer.name, "str"),
    "site": SortOption(Site.name, "str"),
}


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _base(p: Principal) -> Select[tuple[Device]]:
    return (
        select(Device)
        .join(Customer, Customer.id == Device.customer_id)
        .join(Site, Site.id == Device.site_id)
        .where(
            Device.deleted_at.is_(None),
            reseller_scope(p, Device.reseller_id),
            customer_scope(p, Device.customer_id),
        )
    )


async def list_discoveries(
    session: AsyncSession,
    p: Principal,
    *,
    state: str,
    q: str | None,
    customer_id: uuid.UUID | None,
    site_id: uuid.UUID | None,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> tuple[PageResult[Device], int]:
    p.require("devices.read")
    stmt = _base(p).where(Device.discovery_state == state)
    if customer_id:
        stmt = stmt.where(Device.customer_id == customer_id)
    if site_id:
        stmt = stmt.where(Device.site_id == site_id)
    if q:
        like = _like(q)
        stmt = stmt.where(
            or_(
                Device.serial.ilike(like),
                Device.ip.ilike(like),
                Device.model.ilike(like),
                Device.brand.ilike(like),
                Device.sys_location.ilike(like),
                Customer.name.ilike(like),
                Site.name.ilike(like),
            )
        )
    page = await paginate(
        session,
        stmt,
        id_column=Device.id,
        sort_options=DISCOVERY_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    return page, total


async def counts(session: AsyncSession, p: Principal) -> DiscoveryCounts:
    p.require("devices.read")
    base = _base(p).subquery()
    row = (
        await session.execute(
            select(
                func.count().filter(base.c.discovery_state == "pending"),
                func.count().filter(base.c.discovery_state == "discarded"),
            ).select_from(base)
        )
    ).one()
    return DiscoveryCounts(pending=row[0], discarded=row[1])


async def decide(session: AsyncSession, p: Principal, data: DecisionIn) -> DecisionOut:
    target, permission, event = ACTIONS[data.action]
    p.require(permission)
    rows = list(
        (
            await session.execute(_base(p).where(Device.id.in_(data.device_ids)).with_for_update(of=Device))
        ).scalars()
    )
    found = {d.id for d in rows}
    out = DecisionOut(
        changed=0,
        skipped=[
            {"device_id": str(i), "reason": "não encontrado"} for i in data.device_ids if i not in found
        ],
    )
    now = datetime.now(UTC)
    sites: set[uuid.UUID] = set()
    for device in rows:
        if device.discovery_state not in ALLOWED_FROM[data.action]:
            out.skipped.append(
                {"device_id": str(device.id), "reason": f"estado atual: {device.discovery_state}"}
            )
            continue
        before = device.discovery_state
        device.discovery_state = target
        device.discovery_decided_at = now
        device.discovery_decided_by = p.user_id
        session.add(
            DeviceEvent(
                reseller_id=device.reseller_id,
                device_id=device.id,
                type=event,
                data={"from": before, "to": target},
                user_id=p.user_id,
            )
        )
        await audit.record(
            session,
            p,
            action=f"discovery_{data.action}",
            entity="device",
            entity_id=device.id,
            reseller_id=device.reseller_id,
            before={"discovery_state": before},
            after={"discovery_state": target, "serial": device.serial},
        )
        if data.action in {"discard", "restore"} or before == "discarded":
            sites.add(device.site_id)
        out.changed += 1
    if not rows and not out.skipped:
        raise bad_request("nothing_to_do", "Nenhum equipamento selecionado")
    # Descartar/restaurar muda os seriais ignorados: os coletores buscam a configuração de novo.
    for site_id in sites:
        await bump_site_config(session, site_id)
    return out
