"""Computadores (PROMPT 11 / 10.7): PCs com coletor, as impressoras USB de cada um e a leitura manual
(também para qualquer equipamento sem contador por SNMP/PJL)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, not_found
from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import Agent, Customer, Device, Reading, ReadingCounter, Site
from app.models.readings import COUNTER_FIELDS
from app.schemas.computers import ComputerOut, ComputerPage, ManualReadingIn, UsbPrinterOut
from app.services import audit
from app.services import devices as devices_svc
from app.services.counter_lines import resolve_lines
from app.services.pagination import SortOption, paginate
from app.services.reports.counters import cutoff

SORTS = {"name": SortOption(func.lower(Agent.name), "str")}
FUTURE_TOLERANCE = timedelta(minutes=5)


def _query(p: Principal, q: str | None) -> Select[tuple[Agent]]:
    stmt = (
        select(Agent)
        .join(Site, Site.id == Agent.site_id)
        .where(
            Agent.deleted_at.is_(None),
            Agent.enrolled_at.is_not(None),
            reseller_scope(p, Agent.reseller_id),
            customer_scope(p, Site.customer_id),
        )
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Agent.name.ilike(like), Agent.hostname.ilike(like)))
    return stmt


async def list_computers(
    session: AsyncSession, p: Principal, *, q: str | None, cursor: str | None, limit: int
) -> ComputerPage:
    p.require("agents.read")
    page = await paginate(
        session,
        _query(p, q),
        id_column=Agent.id,
        sort_options=SORTS,
        sort="name",
        direction="asc",
        limit=limit,
        cursor=cursor,
    )
    ids = [a.id for a in page.items]
    names = (
        dict(
            (
                await session.execute(
                    select(Agent.id, Customer.name + " / " + Site.name)
                    .join(Site, Site.id == Agent.site_id)
                    .join(Customer, Customer.id == Site.customer_id)
                    .where(Agent.id.in_(ids))
                )
            )
            .tuples()
            .all()
        )
        if ids
        else {}
    )
    usb = (
        dict(
            (
                await session.execute(
                    select(Device.usb_agent_id, func.count())
                    .where(Device.usb_agent_id.in_(ids), Device.deleted_at.is_(None), Device.source == "usb")
                    .group_by(Device.usb_agent_id)
                )
            )
            .tuples()
            .all()
        )
        if ids
        else {}
    )
    items = [
        ComputerOut(
            id=a.id,
            name=a.name,
            hostname=a.hostname,
            os=a.os,
            kind=a.kind,
            version=a.version,
            state=a.state,
            last_seen_at=a.last_seen_at,
            public_ip=a.public_ip,
            local_ips=[str(x) for x in a.local_ips],
            location=names.get(a.id, ""),
            usb_printers=usb.get(a.id, 0),
        )
        for a in page.items
    ]
    return ComputerPage(items=items, next_cursor=page.next_cursor)


async def usb_printers(session: AsyncSession, p: Principal, agent_id: uuid.UUID) -> list[UsbPrinterOut]:
    p.require("devices.read")
    agent = (await session.execute(_query(p, None).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        raise not_found("Computador")
    pjl = exists().where(Reading.device_id == Device.id, Reading.source == "usb")
    rows = await session.execute(
        select(Device, pjl.label("pjl"))
        .where(Device.usb_agent_id == agent.id, Device.source == "usb", Device.deleted_at.is_(None))
        .order_by(Device.model, Device.serial)
    )
    return [
        UsbPrinterOut(
            id=d.id,
            serial=d.serial,
            brand=d.brand,
            model=d.model,
            name=d.sys_descr,
            status=d.last_status,
            last_status_at=d.last_status_at,
            discovery_state=d.discovery_state,
            counter_available=bool(has_pjl),
            last_total=d.last_total,
            last_mono=d.last_mono,
            last_color=d.last_color,
            last_read_at=d.last_read_at,
        )
        for d, has_pjl in rows.tuples()
    ]


async def add_manual_reading(
    session: AsyncSession, p: Principal, device_id: uuid.UUID, data: ManualReadingIn
) -> Reading:
    """Leitura digitada (folha de contadores): valem as mesmas regras dos relatórios; contador menor que o
    da última leitura válida é recusado (ajuste uma leitura errada em vez de criar uma regressão)."""
    p.require("readings.adjust")
    device = await devices_svc.get_device(session, p, device_id)
    now = datetime.now(UTC)
    read_at = data.read_at or now
    if read_at > now + FUTURE_TOLERANCE:
        raise bad_request("future_read_at", "A data da leitura está no futuro")
    values = {"total": data.total, "mono": data.mono, "color": data.color}
    if values["total"] is None and values["mono"] is not None and values["color"] is not None:
        values["total"] = values["mono"] + values["color"]
    if values["total"] is None:
        raise bad_request("total_required", "Informe o total (ou PB e cor)")
    scope = "d.id = :device"
    prev = (await cutoff(session, scope, {"device": device.id}, read_at)).get(device.id)
    if prev is not None:
        for field, label in (("total", "total"), ("mono", "PB"), ("color", "cor")):
            before = getattr(prev, field)
            new = values[field]
            if new is not None and before is not None and new < before:
                raise bad_request(
                    "counter_lower",
                    f"Contador {label} ({new:,}) menor que o da leitura de "
                    f"{prev.read_at:%d/%m/%Y %H:%M} UTC ({before:,}). Se a leitura anterior estava errada, "
                    "ajuste-a no histórico.".replace(",", "."),
                )
    reading = Reading(
        id=uuid.uuid4(),
        read_at=read_at,
        received_at=now,
        reseller_id=device.reseller_id,
        device_id=device.id,
        agent_id=None,
        user_id=p.user_id,
        idempotency_key=f"manual:{uuid.uuid4()}",
        source="manual",
        counter_source="manual",
        extra={"note": data.note} if data.note else {},
        flags=[],
        **{f: values.get(f) for f in COUNTER_FIELDS},
    )
    session.add(reading)
    await session.flush()
    rows, _conflicts = resolve_lines({k: v for k, v in values.items() if v is not None}, {})
    for name, line, value in rows:
        session.add(
            ReadingCounter(
                reading_id=reading.id,
                read_at=read_at,
                reseller_id=device.reseller_id,
                device_id=device.id,
                kind=line.kind,
                color_mode=line.color_mode,
                size=line.size,
                name=name,
                value=value,
            )
        )
    if device.last_read_at is None or read_at >= device.last_read_at:
        device.last_read_at = read_at
        device.last_total, device.last_mono, device.last_color = (
            values["total"],
            values["mono"],
            values["color"],
        )
        device.disconnected = False
    await audit.record(
        session,
        p,
        action="reading.manual",
        entity="device",
        entity_id=device.id,
        reseller_id=device.reseller_id,
        after={"read_at": read_at.isoformat(), **values, "note": data.note},
    )
    return reading
