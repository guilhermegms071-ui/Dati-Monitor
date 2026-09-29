"""Portal side of alerts (PROMPT 10 item 8): list with filters, counts for the bell, acknowledge and
resolve (single or bulk), plus the printer-alert (16.4) and supply-replacement (16.3) screens. Every list
is paginated on the server, newest first."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import Agent, Alert, Customer, Device, PrinterAlert, SupplyReplacement
from app.schemas.alerts import (
    RULE_LABELS,
    AlertActionIn,
    AlertActionOut,
    AlertCounts,
    AlertOut,
    PrinterAlertCounts,
    PrinterAlertOut,
    SupplyReplacementOut,
)
from app.services import audit
from app.services.alerts import emit_changes
from app.services.pagination import Direction, PageResult, SortOption, paginate


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _alert_scope(p: Principal) -> Select[tuple[Alert]]:
    return select(Alert).where(
        reseller_scope(p, Alert.reseller_id),
        true() if p.customer_id is None else Alert.customer_id == p.customer_id,
    )


@dataclass
class AlertFilters:
    state: str | None = None  # open (= não resolvido) | acknowledged | resolved | all
    severity: str | None = None
    type: str | None = None
    customer_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    q: str | None = None


ALERT_SORTS = {
    "opened_at": SortOption(Alert.opened_at, "datetime"),
    "severity": SortOption(Alert.severity, "str"),
    "type": SortOption(Alert.type, "str"),
}


def _filtered(p: Principal, f: AlertFilters) -> Select[tuple[Alert]]:
    p.require("alerts.read")
    stmt = _alert_scope(p)
    if f.state == "open":
        stmt = stmt.where(Alert.state != "resolved")
    elif f.state in ("acknowledged", "resolved"):
        stmt = stmt.where(Alert.state == f.state)
    if f.severity:
        stmt = stmt.where(Alert.severity == f.severity)
    if f.type:
        stmt = stmt.where(Alert.type == f.type)
    if f.customer_id:
        stmt = stmt.where(Alert.customer_id == f.customer_id)
    if f.device_id:
        stmt = stmt.where(Alert.target_type == "device", Alert.target_id == f.device_id)
    if f.agent_id:
        stmt = stmt.where(Alert.target_type == "agent", Alert.target_id == f.agent_id)
    if f.q:
        stmt = stmt.where(Alert.message.ilike(_like(f.q)))
    return stmt


async def enrich(session: AsyncSession, alerts: Sequence[Alert]) -> list[AlertOut]:
    if not alerts:
        return []
    device_ids = {a.target_id for a in alerts if a.target_type == "device"}
    agent_ids = {a.target_id for a in alerts if a.target_type == "agent"}
    customer_ids = {a.customer_id for a in alerts if a.customer_id}
    names: dict[uuid.UUID, str] = {}
    if device_ids:
        for d in (await session.execute(select(Device).where(Device.id.in_(device_ids)))).scalars():
            names[d.id] = " ".join(x for x in (d.model, d.serial) if x)
    if agent_ids:
        names.update(
            dict(
                (await session.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))))
                .tuples()
                .all()
            )
        )
    customers = (
        dict(
            (await session.execute(select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))))
            .tuples()
            .all()
        )
        if customer_ids
        else {}
    )
    out = []
    for a in alerts:
        row = AlertOut.model_validate(a)
        row.type_label = RULE_LABELS.get(a.type, a.type)
        row.target_name = names.get(a.target_id, "")
        row.customer_name = customers.get(a.customer_id, "") if a.customer_id else ""
        out.append(row)
    return out


async def list_alerts(
    session: AsyncSession,
    p: Principal,
    f: AlertFilters,
    *,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> tuple[PageResult[Alert], int]:
    stmt = _filtered(p, f)
    page = await paginate(
        session,
        stmt,
        id_column=Alert.id,
        sort_options=ALERT_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    return page, total


async def counts(session: AsyncSession, p: Principal) -> AlertCounts:
    p.require("alerts.read")
    base = _alert_scope(p).where(Alert.state != "resolved").subquery()
    row = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(base.c.severity == "critical"),
                func.count().filter(base.c.severity == "warning"),
                func.count().filter(base.c.severity == "info"),
            ).select_from(base)
        )
    ).one()
    return AlertCounts(open=row[0], critical=row[1], warning=row[2], info=row[3])


async def get_alert(session: AsyncSession, p: Principal, alert_id: uuid.UUID) -> AlertOut | None:
    row = (
        await session.execute(_filtered(p, AlertFilters()).where(Alert.id == alert_id))
    ).scalar_one_or_none()
    return (await enrich(session, [row]))[0] if row else None


async def act(session: AsyncSession, p: Principal, data: AlertActionIn) -> AlertActionOut:
    """Reconhecer (alguém está cuidando) ou resolver; resolvidos não voltam a ser reconhecidos."""
    p.require("alerts.manage")
    now = datetime.now(UTC)
    target = _alert_scope(p).where(Alert.id.in_(data.alert_ids), Alert.state != "resolved")
    if data.action == "acknowledge":
        target = target.where(Alert.state == "open")
        values = {"state": "acknowledged", "acknowledged_at": now, "acknowledged_by": p.user_id}
    else:
        values = {"state": "resolved", "resolved_at": now, "resolved_by": p.user_id}
    ids = [a.id for a in (await session.execute(target)).scalars()]
    if not ids:
        return AlertActionOut(changed=0)
    rows = (
        (await session.execute(update(Alert).where(Alert.id.in_(ids)).values(**values).returning(Alert)))
        .scalars()
        .all()
    )
    for a in rows:
        await audit.record(
            session,
            p,
            action=f"alert_{data.action}",
            entity="alert",
            entity_id=a.id,
            reseller_id=a.reseller_id,
            after={"type": a.type, "message": a.message},
        )
    await emit_changes(
        session,
        {(a.reseller_id, a.customer_id) for a in rows} | {(a.reseller_id, None) for a in rows},
        0,
        len(rows) if data.action == "resolve" else 0,
    )
    return AlertActionOut(changed=len(rows))


# ----------------------------------------------------------------------------- impressora e suprimentos


def _device_join(stmt: Select[Any], p: Principal, device_col: Any) -> Select[Any]:
    return (
        stmt.join(Device, Device.id == device_col)
        .join(Customer, Customer.id == Device.customer_id)
        .where(
            Device.deleted_at.is_(None),
            reseller_scope(p, Device.reseller_id),
            customer_scope(p, Device.customer_id),
        )
    )


PRINTER_SORTS = {"first_seen_at": SortOption(PrinterAlert.first_seen_at, "datetime")}
REPLACEMENT_SORTS = {"replaced_at": SortOption(SupplyReplacement.replaced_at, "datetime")}


async def list_printer_alerts(
    session: AsyncSession,
    p: Principal,
    *,
    category: str | None,
    active: bool | None,
    customer_id: uuid.UUID | None,
    device_id: uuid.UUID | None,
    q: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[PrinterAlertOut], str | None, int]:
    p.require("devices.read")
    stmt = _device_join(select(PrinterAlert), p, PrinterAlert.device_id)
    if category:
        stmt = stmt.where(PrinterAlert.category == category)
    if active is True:
        stmt = stmt.where(PrinterAlert.cleared_at.is_(None))
    elif active is False:
        stmt = stmt.where(PrinterAlert.cleared_at.is_not(None))
    if customer_id:
        stmt = stmt.where(Device.customer_id == customer_id)
    if device_id:
        stmt = stmt.where(PrinterAlert.device_id == device_id)
    if q:
        like = _like(q)
        stmt = stmt.where(
            or_(PrinterAlert.description.ilike(like), Device.serial.ilike(like), Customer.name.ilike(like))
        )
    page = await paginate(
        session,
        stmt,
        id_column=PrinterAlert.id,
        sort_options=PRINTER_SORTS,
        sort="first_seen_at",
        direction="desc",
        limit=limit,
        cursor=cursor,
    )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    info = await _device_info(session, {r.device_id for r in page.items})
    items = []
    for r in page.items:
        row = PrinterAlertOut.model_validate(r)
        row.serial, row.model, row.customer_name = info.get(r.device_id, ("", None, ""))
        items.append(row)
    return items, page.next_cursor, total


async def printer_alert_counts(session: AsyncSession, p: Principal) -> PrinterAlertCounts:
    p.require("devices.read")
    stmt = _device_join(select(PrinterAlert.category, func.count()), p, PrinterAlert.device_id).where(
        PrinterAlert.cleared_at.is_(None), Device.discovery_state == "approved"
    )
    rows = dict((await session.execute(stmt.group_by(PrinterAlert.category))).tuples().all())
    return PrinterAlertCounts(
        **{c: rows.get(c, 0) for c in ("parts", "service_call", "jam", "consumable", "other")}
    )


async def list_replacements(
    session: AsyncSession,
    p: Principal,
    *,
    customer_id: uuid.UUID | None,
    device_id: uuid.UUID | None,
    color: str | None,
    premature: bool | None,
    date_from: datetime | None,
    date_to: datetime | None,
    q: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[SupplyReplacementOut], str | None, int]:
    p.require("devices.read")
    stmt = _device_join(select(SupplyReplacement), p, SupplyReplacement.device_id)
    if customer_id:
        stmt = stmt.where(Device.customer_id == customer_id)
    if device_id:
        stmt = stmt.where(SupplyReplacement.device_id == device_id)
    if color:
        stmt = stmt.where(SupplyReplacement.color == color)
    if premature is not None:
        stmt = stmt.where(SupplyReplacement.premature.is_(premature))
    if date_from:
        stmt = stmt.where(SupplyReplacement.replaced_at >= date_from)
    if date_to:
        stmt = stmt.where(SupplyReplacement.replaced_at <= date_to)
    if q:
        like = _like(q)
        stmt = stmt.where(or_(Device.serial.ilike(like), Device.model.ilike(like), Customer.name.ilike(like)))
    page = await paginate(
        session,
        stmt,
        id_column=SupplyReplacement.id,
        sort_options=REPLACEMENT_SORTS,
        sort="replaced_at",
        direction="desc",
        limit=limit,
        cursor=cursor,
    )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    info = await _device_info(session, {r.device_id for r in page.items})
    items = []
    for r in page.items:
        row = SupplyReplacementOut.model_validate(r)
        row.serial, row.model, row.customer_name = info.get(r.device_id, ("", None, ""))
        items.append(row)
    return items, page.next_cursor, total


async def _device_info(
    session: AsyncSession, ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str | None, str]]:
    if not ids:
        return {}
    rows = await session.execute(
        select(Device.id, Device.serial, Device.model, Customer.name)
        .join(Customer, Customer.id == Device.customer_id)
        .where(Device.id.in_(ids))
    )
    return {i: (s, m, c) for i, s, m, c in rows.tuples()}
