"""Park screen (PROMPT 10.6): filtered/sorted device list for thousands of rows, counts, export, bulk
actions, device editing, counter series, supply history and manual reading adjustments."""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.errors import bad_request, not_found
from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import (
    Agent,
    Customer,
    Device,
    DeviceEvent,
    Reading,
    ReadingAdjustment,
    Site,
    SupplyCurrent,
    SupplyReading,
    User,
)
from app.schemas.commands import CommandIn
from app.schemas.park import (
    AdjustmentIn,
    AdjustmentOut,
    BulkDevicesIn,
    BulkDevicesOut,
    CounterPoint,
    DeviceUpdate,
    ParkCounts,
    ParkRow,
    SupplyLevel,
    SupplyPoint,
)
from app.services import audit
from app.services import commands as commands_svc
from app.services import custom_fields as custom_fields_svc
from app.services import devices as devices_svc
from app.services.pagination import Direction, PageResult, SortOption, paginate

LastAgent = aliased(Agent)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Cores exibidas na coluna "Níveis", nesta ordem.
LEVEL_COLORS = ("cyan", "magenta", "yellow", "black")
# Toner baixo na tela Parque (barra vermelha e aba "Com alerta").
LOW_TONER_PERCENT = 10
# Comunicação instável: N das últimas M leituras precisaram de mais tentativas SNMP que o padrão.
UNSTABLE_MIN, UNSTABLE_WINDOW = 3, 5
UNSTABLE_LOOKBACK = timedelta(days=30)
DISPLAY_TZ = "America/Sao_Paulo"


@dataclass(frozen=True)
class ParkFilters:
    q: str | None = None
    status: Sequence[str] = ()
    serial: str | None = None
    ip: str | None = None
    brand: str | None = None
    model: str | None = None
    sector: str | None = None
    asset_tag: str | None = None
    customer_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    disconnected: bool = False  # só desconectados
    inactive: bool = False  # só desativados (senão: só ativos)
    alert: bool = False  # só com erro, atenção ou toner baixo


def _like(value: str) -> str:
    return "%" + value.replace("%", "").replace("_", "").strip() + "%"


def _base(p: Principal) -> Select[tuple[Device]]:
    return (
        select(Device)
        .join(Customer, Customer.id == Device.customer_id)
        .join(Site, Site.id == Device.site_id)
        .outerjoin(LastAgent, LastAgent.id == Device.last_agent_id)
        .where(
            Device.deleted_at.is_(None),
            Device.discovery_state == "approved",  # pendentes e descartados ficam em Descobertas (16.1)
            reseller_scope(p, Device.reseller_id),
            customer_scope(p, Device.customer_id),
        )
    )


def _alert_condition() -> Any:
    """Aba "Com alerta": erro, atenção ou algum toner (C/M/Y/K) abaixo de LOW_TONER_PERCENT."""
    low_toner = (
        select(SupplyCurrent.device_id)
        .where(
            SupplyCurrent.device_id == Device.id,
            SupplyCurrent.supply_class == "consumed",
            SupplyCurrent.color.in_(LEVEL_COLORS),
            SupplyCurrent.percent < LOW_TONER_PERCENT,
        )
        .exists()
    )
    return or_(Device.last_status.in_(("error", "warning")), low_toner)


def _filtered(p: Principal, f: ParkFilters) -> Select[tuple[Device]]:
    p.require("devices.read")
    stmt = _base(p).where(Device.active.is_(not f.inactive))
    if f.disconnected:
        stmt = stmt.where(Device.disconnected.is_(True))
    if f.status:
        stmt = stmt.where(Device.last_status.in_(list(f.status)))
    if f.alert:
        stmt = stmt.where(_alert_condition())
    for column, value in (
        (Device.serial, f.serial),
        (Device.ip, f.ip),
        (Device.brand, f.brand),
        (Device.model, f.model),
        (Device.sector, f.sector),
        (Device.asset_tag, f.asset_tag),
    ):
        if value:
            stmt = stmt.where(column.ilike(_like(value)))
    if f.customer_id:
        stmt = stmt.where(Device.customer_id == f.customer_id)
    if f.site_id:
        stmt = stmt.where(Device.site_id == f.site_id)
    if f.agent_id:
        stmt = stmt.where(Device.last_agent_id == f.agent_id)
    if f.q:
        like = _like(f.q)
        stmt = stmt.where(
            or_(
                Device.serial.ilike(like),
                Device.ip.ilike(like),
                Device.model.ilike(like),
                Device.brand.ilike(like),
                Device.hostname.ilike(like),
                Device.asset_tag.ilike(like),
                Device.sector.ilike(like),
                Customer.name.ilike(like),
                Site.name.ilike(like),
            )
        )
    return stmt


PARK_SORTS = {
    "status": SortOption(Device.last_status, "str"),
    "ip": SortOption(func.coalesce(Device.ip, ""), "str"),
    "agent": SortOption(func.coalesce(LastAgent.name, ""), "str"),
    "first_seen_at": SortOption(Device.first_seen_at, "datetime"),
    "last_read_at": SortOption(func.coalesce(Device.last_read_at, EPOCH), "datetime"),
    "asset_tag": SortOption(func.coalesce(Device.asset_tag, ""), "str"),
    "serial": SortOption(Device.serial, "str"),
    "brand": SortOption(func.coalesce(Device.brand, ""), "str"),
    "model": SortOption(func.coalesce(Device.model, ""), "str"),
    "sector": SortOption(func.coalesce(Device.sector, ""), "str"),
    "customer": SortOption(Customer.name, "str"),
    "site": SortOption(Site.name, "str"),
    "total": SortOption(func.coalesce(Device.last_total, -1), "int"),
    "mono": SortOption(func.coalesce(Device.last_mono, -1), "int"),
    "color": SortOption(func.coalesce(Device.last_color, -1), "int"),
}


async def list_park(
    session: AsyncSession,
    p: Principal,
    f: ParkFilters,
    *,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> tuple[PageResult[Device], int]:
    stmt = _filtered(p, f)
    page = await paginate(
        session,
        stmt,
        id_column=Device.id,
        sort_options=PARK_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    return page, total


async def _names(session: AsyncSession, stmt: Select[tuple[uuid.UUID, str]]) -> dict[uuid.UUID, str]:
    return dict((await session.execute(stmt)).tuples().all())


async def enrich(session: AsyncSession, devices: Sequence[Device]) -> list[ParkRow]:
    """Adds customer/site/agent names and the C/M/Y/K levels to each row (3 queries per page)."""
    if not devices:
        return []
    ids = [d.id for d in devices]
    customers = await _names(
        session, select(Customer.id, Customer.name).where(Customer.id.in_({d.customer_id for d in devices}))
    )
    sites = await _names(session, select(Site.id, Site.name).where(Site.id.in_({d.site_id for d in devices})))
    agent_ids = {d.last_agent_id for d in devices if d.last_agent_id}
    agents = (
        await _names(session, select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))
        if agent_ids
        else {}
    )
    levels: dict[uuid.UUID, dict[str, SupplyLevel]] = defaultdict(dict)
    rows = await session.execute(
        select(SupplyCurrent).where(
            SupplyCurrent.device_id.in_(ids),
            SupplyCurrent.supply_class == "consumed",
            SupplyCurrent.color.in_(LEVEL_COLORS),
        )
    )
    for s in rows.scalars():
        color = s.color or ""
        cur = levels[s.device_id].get(color)
        # Mais de um suprimento da mesma cor (toner + cilindro): a barra mostra o toner (tipo "toner").
        if cur is None or s.supply_type == "toner":
            levels[s.device_id][color] = SupplyLevel(
                color=color,
                percent=float(s.percent) if s.percent is not None else None,
                level_state=s.level_state,
                description=s.description,
            )
    unstable = await _unstable(session, ids)
    out = []
    for d in devices:
        base = ParkRow.model_validate(
            {
                **{k: getattr(d, k) for k in ParkRow.model_fields if hasattr(d, k)},
                "customer_name": customers.get(d.customer_id, ""),
                "site_name": sites.get(d.site_id, ""),
                "agent_name": agents.get(d.last_agent_id) if d.last_agent_id else None,
                "supplies": [levels[d.id][c] for c in LEVEL_COLORS if c in levels[d.id]],
                "comm_unstable": d.id in unstable,
            }
        )
        out.append(base)
    return out


async def counts(session: AsyncSession, settings: Settings, p: Principal) -> ParkCounts:
    p.require("devices.read")
    base = _base(p).add_columns(_alert_condition().label("alert")).subquery()
    row = (
        await session.execute(
            select(
                func.count().filter(base.c.active.is_(True)),
                func.count().filter(and_(base.c.active.is_(True), base.c.disconnected.is_(True))),
                func.count().filter(base.c.active.is_(False)),
                func.count().filter(and_(base.c.active.is_(True), base.c.alert.is_(True))),
            ).select_from(base)
        )
    ).one()
    return ParkCounts(
        total=row[0],
        disconnected=row[1],
        inactive=row[2],
        alert=row[3],
        disconnected_hours=settings.device_disconnected_hours,
    )


async def _unstable(session: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Equipamentos cujas últimas leituras precisaram de mais tentativas SNMP que o padrão do coletor."""
    tr = Reading.extra["transport"]
    recent = (
        select(
            Reading.device_id,
            (tr["max_retries"].as_integer() > tr["base_retries"].as_integer()).label("hard"),
            func.row_number()
            .over(partition_by=Reading.device_id, order_by=Reading.read_at.desc())
            .label("n"),
        )
        .where(
            Reading.device_id.in_(ids),
            Reading.read_at >= datetime.now(UTC) - UNSTABLE_LOOKBACK,
        )
        .subquery()
    )
    rows = await session.execute(
        select(recent.c.device_id)
        .where(recent.c.n <= UNSTABLE_WINDOW, recent.c.hard.is_(True))
        .group_by(recent.c.device_id)
        .having(func.count() >= UNSTABLE_MIN)
    )
    return set(rows.scalars())


async def export_rows(
    session: AsyncSession, p: Principal, f: ParkFilters, sort: str, direction: Direction
) -> list[ParkRow]:
    if sort not in PARK_SORTS:
        raise bad_request("invalid_sort", f"Ordenação inválida: {sort}")
    expr = PARK_SORTS[sort].expression
    order = (expr.asc(), Device.id.asc()) if direction == "asc" else (expr.desc(), Device.id.desc())
    # Sem limite fixo de linhas (seção 0, regra 13): a exportação respeita só os filtros.
    devices = list((await session.execute(_filtered(p, f).order_by(*order))).scalars())
    return await enrich(session, devices)


# ----------------------------------------------------------------------------- edição


async def _site_for_move(session: AsyncSession, p: Principal, device: Device, site_id: uuid.UUID) -> Site:
    site = await session.get(Site, site_id)
    if (
        site is None
        or site.deleted_at is not None
        or site.reseller_id != device.reseller_id
        or not p.can_access_customer(site.reseller_id, site.customer_id)
    ):
        raise bad_request("invalid_site", "Local de destino inválido")
    return site


async def update_device(
    session: AsyncSession, p: Principal, device_id: uuid.UUID, data: DeviceUpdate
) -> Device:
    p.require("devices.update")
    device = await devices_svc.get_device(session, p, device_id)
    before = audit.snapshot(device)
    await _apply(session, p, device, data.model_dump(exclude_unset=True))
    await session.flush()
    b, a = audit.diff(before, audit.snapshot(device))
    if a:
        await audit.record(
            session,
            p,
            action="update",
            entity="device",
            entity_id=device.id,
            reseller_id=device.reseller_id,
            before=b,
            after=a,
        )
    return device


# Campos de cobrança e limiar de toner: numéricos/estruturados, gravados como vieram (já validados).
_PLAIN_FIELDS = (
    "franchise_value",
    "franchise_pages_mono",
    "franchise_pages_color",
    "overage_price_mono",
    "overage_price_color",
)


async def _apply(session: AsyncSession, p: Principal, device: Device, values: dict[str, Any]) -> None:
    site_id = values.pop("site_id", None)
    for key in ("asset_tag", "notes", "alt_serial"):
        if key in values:
            v = values[key]
            setattr(device, key, v.strip() or None if isinstance(v, str) else v)
    if "sector" in values:
        sector = (values["sector"] or "").strip()
        # Setor digitado deixa de seguir o sysLocation; vazio volta a segui-lo (seção 16.7).
        device.sector_from_snmp = not sector
        device.sector = sector or device.sys_location
    await _apply_contract(session, p, device, values)
    if values.get("monitored") is not None:
        device.monitored = bool(values["monitored"])
    await _apply_state(session, p, device, values, site_id)


async def _apply_contract(
    session: AsyncSession, p: Principal, device: Device, values: dict[str, Any]
) -> None:
    """Franquia, excedente, campos personalizados e limiar de toner (16.5/16.7)."""
    for key in _PLAIN_FIELDS:
        if key in values:
            setattr(device, key, values[key])
    if values.get("custom_fields") is not None:
        device.custom_fields = await custom_fields_svc.validate_values(
            session, device.reseller_id, device.custom_fields or {}, values["custom_fields"]
        )
    if values.get("toner_mode") is not None or values.get("toner_thresholds") is not None:
        p.require("supplies.monitor")
        if values.get("toner_mode") is not None:
            device.toner_mode = values["toner_mode"]
        if values.get("toner_thresholds") is not None:
            device.toner_thresholds = dict(values["toner_thresholds"])
        if device.toner_mode == "individual" and not device.toner_thresholds:
            raise bad_request("toner_thresholds_required", "Informe os limiares próprios do equipamento")


async def _apply_state(
    session: AsyncSession, p: Principal, device: Device, values: dict[str, Any], site_id: uuid.UUID | None
) -> None:
    if values.get("active") is not None and bool(values["active"]) != device.active:
        device.active = bool(values["active"])
        session.add(
            DeviceEvent(
                reseller_id=device.reseller_id,
                device_id=device.id,
                type="reactivated" if device.active else "deactivated",
                data={"manual": True},
                user_id=p.user_id,
            )
        )
    if site_id and site_id != device.site_id:
        site = await _site_for_move(session, p, device, site_id)
        session.add(
            DeviceEvent(
                reseller_id=device.reseller_id,
                device_id=device.id,
                type="moved_site",
                data={"manual": True, "from_site_id": str(device.site_id), "to_site_id": str(site.id)},
                user_id=p.user_id,
            )
        )
        device.site_id, device.customer_id = site.id, site.customer_id


async def bulk(
    session: AsyncSession, settings: Settings, p: Principal, data: BulkDevicesIn
) -> BulkDevicesOut:
    p.require("devices.read")
    rows = list(
        (
            await session.execute(
                select(Device).where(
                    Device.id.in_(data.device_ids),
                    Device.deleted_at.is_(None),
                    reseller_scope(p, Device.reseller_id),
                    customer_scope(p, Device.customer_id),
                )
            )
        ).scalars()
    )
    found = {d.id for d in rows}
    out = BulkDevicesOut(
        changed=0,
        skipped=[
            {"device_id": str(i), "reason": "não encontrado"} for i in data.device_ids if i not in found
        ],
    )
    if data.action == "read_now":
        return await _bulk_read(session, settings, p, rows, out)
    p.require("devices.update")
    values: dict[str, Any] = {}
    match data.action:
        case "activate" | "deactivate":
            values["active"] = data.action == "activate"
        case "monitor" | "unmonitor":
            values["monitored"] = data.action == "monitor"
        case "update":
            if data.sector is None and data.asset_tag is None:
                raise bad_request("nothing_to_update", "Informe o setor e/ou o PAT")
            if data.sector is not None:
                values["sector"] = data.sector
            if data.asset_tag is not None:
                values["asset_tag"] = data.asset_tag
        case "move":
            if not data.site_id:
                raise bad_request("site_required", "Informe o local de destino")
            values["site_id"] = data.site_id
    for device in rows:
        before = audit.snapshot(device)
        await _apply(session, p, device, dict(values))
        b, a = audit.diff(before, audit.snapshot(device))
        if a:
            out.changed += 1
            await audit.record(
                session,
                p,
                action=f"bulk_{data.action}",
                entity="device",
                entity_id=device.id,
                reseller_id=device.reseller_id,
                before=b,
                after=a,
            )
    await session.flush()
    return out


async def _bulk_read(
    session: AsyncSession, settings: Settings, p: Principal, rows: list[Device], out: BulkDevicesOut
) -> BulkDevicesOut:
    """read_now goes to the MASTER of each site (network printers) or to the PC the USB printer is
    plugged into (only that PC sees it, MASTER or STANDBY)."""
    by_agent: dict[uuid.UUID, list[Device]] = defaultdict(list)
    masters: dict[uuid.UUID, Agent | None] = {}
    for d in rows:
        if d.source == "usb" and d.usb_agent_id:
            usb_agent = await session.get(Agent, d.usb_agent_id)
            if usb_agent is None or usb_agent.revoked_at is not None or usb_agent.deleted_at is not None:
                out.skipped.append(
                    {"device_id": str(d.id), "reason": "o PC desta impressora USB não tem mais coletor"}
                )
                continue
            by_agent[usb_agent.id].append(d)
            continue
        if d.site_id not in masters:
            site = await session.get(Site, d.site_id)
            master = await session.get(Agent, site.master_agent_id) if site and site.master_agent_id else None
            ok = master is not None and master.revoked_at is None and master.deleted_at is None
            masters[d.site_id] = master if ok else None
        master = masters[d.site_id]
        if master is None:
            out.skipped.append({"device_id": str(d.id), "reason": "o local não tem coletor MASTER"})
            continue
        by_agent[master.id].append(d)
    for agent_id, devices in by_agent.items():
        cmd = await commands_svc.create_command(
            session,
            settings,
            p,
            agent_id,
            CommandIn(type="read_now", params={"device_ids": [str(d.id) for d in devices]}),
        )
        out.commands.append(cmd.id)
        out.changed += len(devices)
    return out


async def mark_disconnected(session: AsyncSession, hours: int) -> tuple[int, int]:
    """Worker job (PROMPT 8): devices without a reading for `hours` become disconnected; a new reading
    clears it (ingestion). Returns (newly disconnected, reconnected)."""
    limit = datetime.now(UTC) - timedelta(hours=hours)
    stale = or_(
        Device.last_read_at < limit, and_(Device.last_read_at.is_(None), Device.first_seen_at < limit)
    )
    newly = await session.execute(
        update(Device)
        .where(Device.deleted_at.is_(None), Device.active.is_(True), Device.disconnected.is_(False), stale)
        .values(disconnected=True)
        .returning(Device.id)
    )
    back = await session.execute(
        update(Device)
        .where(Device.disconnected.is_(True), Device.last_read_at >= limit)
        .values(disconnected=False)
        .returning(Device.id)
    )
    return len(newly.all()), len(back.all())


# ----------------------------------------------------------------------------- detalhe


async def counter_series(
    session: AsyncSession, p: Principal, device_id: uuid.UUID, *, granularity: str, periods: int
) -> list[CounterPoint]:
    """Last counters of each day/month (in America/Sao_Paulo) and the pages printed in the period."""
    d = await devices_svc.get_device(session, p, device_id)
    unit = "day" if granularity == "day" else "month"
    start = datetime.now(UTC) - (
        timedelta(days=periods + 1) if unit == "day" else timedelta(days=31 * (periods + 1))
    )
    bucket = func.date_trunc(unit, func.timezone(DISPLAY_TZ, Reading.read_at)).label("bucket")
    rows = (
        await session.execute(
            select(bucket, func.max(Reading.total), func.max(Reading.mono), func.max(Reading.color))
            .where(Reading.device_id == d.id, Reading.read_at >= start)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()
    out: list[CounterPoint] = []
    prev: tuple[int | None, int | None, int | None] | None = None
    for b, total, mono, color in rows:

        def delta(cur: int | None, old: int | None) -> int | None:
            if cur is None or old is None:
                return None
            return max(cur - old, 0)

        out.append(
            CounterPoint(
                period=b.date(),
                total=total,
                mono=mono,
                color=color,
                pages=delta(total, prev[0]) if prev else None,
                pages_mono=delta(mono, prev[1]) if prev else None,
                pages_color=delta(color, prev[2]) if prev else None,
            )
        )
        prev = (total, mono, color)
    return out[-periods:]


async def supply_history(
    session: AsyncSession, p: Principal, device_id: uuid.UUID, days: int
) -> list[SupplyPoint]:
    d = await devices_svc.get_device(session, p, device_id)
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await session.execute(
        select(SupplyReading)
        .where(SupplyReading.device_id == d.id, SupplyReading.read_at >= since)
        .order_by(SupplyReading.read_at)
        .limit(20_000)
    )
    return [
        SupplyPoint(
            read_at=r.read_at,
            supply_key=r.supply_key,
            color=r.color,
            percent=r.percent,
            level_state=r.level_state,
        )
        for r in rows.scalars()
    ]


async def add_adjustment(
    session: AsyncSession, p: Principal, device_id: uuid.UUID, data: AdjustmentIn
) -> ReadingAdjustment:
    """R6: readings are never edited; a correction is a separate record with author and reason."""
    p.require("readings.adjust")
    d = await devices_svc.get_device(session, p, device_id)
    reading = (
        await session.execute(
            select(Reading).where(
                Reading.id == data.reading_id, Reading.read_at == data.read_at, Reading.device_id == d.id
            )
        )
    ).scalar_one_or_none()
    if reading is None:
        raise not_found("Leitura")
    if data.total is None and data.mono is None and data.color is None:
        raise bad_request("nothing_to_adjust", "Informe ao menos um contador corrigido")
    adj = ReadingAdjustment(
        reseller_id=d.reseller_id,
        device_id=d.id,
        reading_id=reading.id,
        read_at=reading.read_at,
        total=data.total,
        mono=data.mono,
        color=data.color,
        reason=data.reason,
        user_id=p.user_id,
    )
    session.add(adj)
    await session.flush()
    session.add(
        DeviceEvent(
            reseller_id=d.reseller_id,
            device_id=d.id,
            type="manual_adjust",
            data={
                "reading_id": str(reading.id),
                "before": {"total": reading.total, "mono": reading.mono, "color": reading.color},
                "after": {"total": data.total, "mono": data.mono, "color": data.color},
                "reason": data.reason,
            },
            user_id=p.user_id,
        )
    )
    await audit.record(
        session,
        p,
        action="reading.adjust",
        entity="device",
        entity_id=d.id,
        reseller_id=d.reseller_id,
        after={
            "reading_id": str(reading.id),
            "total": data.total,
            "mono": data.mono,
            "color": data.color,
            "reason": data.reason,
        },
    )
    return adj


async def list_adjustments(session: AsyncSession, p: Principal, device_id: uuid.UUID) -> list[AdjustmentOut]:
    d = await devices_svc.get_device(session, p, device_id)
    rows = await session.execute(
        select(ReadingAdjustment, User.name)
        .outerjoin(User, User.id == ReadingAdjustment.user_id)
        .where(ReadingAdjustment.device_id == d.id)
        .order_by(ReadingAdjustment.created_at.desc())
        .limit(500)
    )
    return [
        AdjustmentOut(
            id=a.id,
            device_id=a.device_id,
            reading_id=a.reading_id,
            read_at=a.read_at,
            total=a.total,
            mono=a.mono,
            color=a.color,
            reason=a.reason,
            user_id=a.user_id,
            user_name=name,
            created_at=a.created_at,
        )
        for a, name in rows.tuples()
    ]


async def readings_for_export(
    session: AsyncSession,
    p: Principal,
    device_id: uuid.UUID,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[Reading]:
    d = await devices_svc.get_device(session, p, device_id)
    stmt = select(Reading).where(Reading.device_id == d.id)
    if date_from:
        stmt = stmt.where(Reading.read_at >= date_from)
    if date_to:
        stmt = stmt.where(Reading.read_at < date_to)
    return list((await session.execute(stmt.order_by(Reading.read_at.desc()).limit(100_000))).scalars())
