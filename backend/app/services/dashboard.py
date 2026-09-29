"""Dashboard (PROMPT 10.2): cards, pages per day (PB x cor), collectors offline now, critical toners."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, literal_column, select, text, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import Agent, Alert, Customer, Device, Site, SupplyCurrent
from app.schemas.park import (
    CriticalSupply,
    Dashboard,
    DashboardCards,
    OfflineAgent,
    PagesPerDay,
    TonersByColor,
)

CRITICAL_PERCENT = 10
# Previsão só entra no painel com confiança suficiente (16.6: incerta não é exibida como certa).
MIN_CONFIDENCE = 0.5
TONER_TYPES = ("toner", "tonerCartridge", "ink", "inkCartridge")
DISPLAY_TZ = "America/Sao_Paulo"
ONLINE_STATES = ("online", "degraded", "paused")


async def build(session: AsyncSession, p: Principal, days: int = 30) -> Dashboard:
    p.require("devices.read")
    dev_scope = and_(
        Device.deleted_at.is_(None),
        Device.discovery_state == "approved",  # pendentes/descartados não contam (16.1)
        reseller_scope(p, Device.reseller_id),
        customer_scope(p, Device.customer_id),
    )
    row = (
        await session.execute(
            select(
                func.count().filter(and_(Device.active.is_(True), Device.monitored.is_(True))),
                func.count().filter(and_(Device.active.is_(True), Device.disconnected.is_(False))),
                func.count().filter(and_(Device.active.is_(True), Device.disconnected.is_(True))),
            ).where(dev_scope)
        )
    ).one()

    agent_base = (
        select(Agent)
        .join(Site, Site.id == Agent.site_id)
        .where(
            Agent.deleted_at.is_(None),
            Agent.revoked_at.is_(None),
            Agent.enrolled_at.is_not(None),
            reseller_scope(p, Agent.reseller_id),
            customer_scope(p, Site.customer_id),
        )
    ).subquery()
    arow = (
        await session.execute(
            select(
                func.count().filter(agent_base.c.state.in_(ONLINE_STATES)),
                func.count().filter(agent_base.c.state == "offline"),
            ).select_from(agent_base)
        )
    ).one()

    alerts_open = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.state != "resolved",
                reseller_scope(p, Alert.reseller_id),
                true() if p.customer_id is None else Alert.customer_id == p.customer_id,
            )
        )
    ).scalar_one()

    critical_q = (
        select(SupplyCurrent, Device.serial, Device.model, Customer.name)
        .join(Device, Device.id == SupplyCurrent.device_id)
        .join(Customer, Customer.id == Device.customer_id)
        .where(
            dev_scope,
            Device.active.is_(True),
            SupplyCurrent.supply_class == "consumed",
            SupplyCurrent.percent <= CRITICAL_PERCENT,
        )
    )
    critical_total = (
        await session.execute(select(func.count()).select_from(critical_q.subquery()))
    ).scalar_one()
    critical = [
        CriticalSupply(
            device_id=s.device_id,
            serial=serial,
            model=model,
            customer_name=cname,
            color=s.color,
            description=s.description,
            percent=s.percent,
            days_to_empty=s.days_to_empty,
        )
        for s, serial, model, cname in (
            await session.execute(critical_q.order_by(SupplyCurrent.percent, Device.serial).limit(20))
        ).tuples()
    ]

    offline = [
        OfflineAgent(
            id=a.id,
            name=a.name,
            customer_name=cname,
            site_name=sname,
            last_seen_at=a.last_seen_at,
            hostname=a.hostname,
        )
        for a, sname, cname in (
            await session.execute(
                select(Agent, Site.name, Customer.name)
                .join(Site, Site.id == Agent.site_id)
                .join(Customer, Customer.id == Site.customer_id)
                .where(
                    Agent.deleted_at.is_(None),
                    Agent.revoked_at.is_(None),
                    Agent.enrolled_at.is_not(None),
                    Agent.state == "offline",
                    reseller_scope(p, Agent.reseller_id),
                    customer_scope(p, Site.customer_id),
                )
                .order_by(Agent.last_seen_at.desc().nulls_last())
                .limit(20)
            )
        ).tuples()
    ]

    return Dashboard(
        cards=DashboardCards(
            devices_monitored=row[0],
            devices_online=row[1],
            devices_disconnected=row[2],
            agents_online=arow[0],
            agents_offline=arow[1],
            alerts_open=alerts_open,
            toners_critical=critical_total,
        ),
        pages_per_day=await pages_per_day(session, p, days),
        offline_agents=offline,
        critical_supplies=critical,
        ending_7_days=await _ending(session, dev_scope, 7),
        ending_30_days_by_color=await _by_color(session, dev_scope, 30),
    )


def _forecast_filter(days: int) -> Any:
    return and_(
        SupplyCurrent.supply_class == "consumed",
        SupplyCurrent.supply_type.in_(TONER_TYPES),
        SupplyCurrent.days_to_empty.is_not(None),
        SupplyCurrent.days_to_empty <= days,
        SupplyCurrent.forecast_confidence >= MIN_CONFIDENCE,
        Device.active.is_(True),
    )


async def _ending(session: AsyncSession, dev_scope: Any, days: int) -> list[CriticalSupply]:
    rows = await session.execute(
        select(SupplyCurrent, Device.serial, Device.model, Customer.name)
        .join(Device, Device.id == SupplyCurrent.device_id)
        .join(Customer, Customer.id == Device.customer_id)
        .where(dev_scope, _forecast_filter(days))
        .order_by(SupplyCurrent.days_to_empty, Device.serial)
        .limit(20)
    )
    return [
        CriticalSupply(
            device_id=s.device_id,
            serial=serial,
            model=model,
            customer_name=cname,
            color=s.color,
            description=s.description,
            percent=s.percent,
            days_to_empty=s.days_to_empty,
            days_to_empty_min=s.days_to_empty_min,
            days_to_empty_max=s.days_to_empty_max,
            forecast_confidence=s.forecast_confidence,
        )
        for s, serial, model, cname in rows.tuples()
    ]


async def _by_color(session: AsyncSession, dev_scope: Any, days: int) -> TonersByColor:
    color = func.coalesce(SupplyCurrent.color, literal_column("'black'"))  # sem parâmetro: GROUP BY igual
    rows = await session.execute(
        select(color, func.count())
        .join(Device, Device.id == SupplyCurrent.device_id)
        .where(dev_scope, _forecast_filter(days))
        .group_by(color)
    )
    counts = dict(rows.tuples().all())
    return TonersByColor(**{c: counts.get(c, 0) for c in ("black", "cyan", "magenta", "yellow")})


async def pages_per_day(session: AsyncSession, p: Principal, days: int) -> list[PagesPerDay]:
    """Sum over devices of the daily increase of the PB and color counters (last reading of each day
    vs. last reading of the previous day), in São Paulo days. Regressions count as zero."""
    since = datetime.now(UTC) - timedelta(days=days + 1)
    scope = "true" if p.is_superadmin else "r.reseller_id = :reseller"
    cust = "" if p.customer_id is None else " AND d.customer_id = :customer"
    sql = text(
        f"""
        WITH daily AS (
            SELECT r.device_id,
                   (r.read_at AT TIME ZONE :tz)::date AS day,
                   max(r.mono) AS mono,
                   max(r.color) AS color
            FROM readings r
            JOIN devices d ON d.id = r.device_id AND d.deleted_at IS NULL
                AND d.discovery_state = 'approved'{cust}
            WHERE r.read_at >= :since AND {scope}
            GROUP BY r.device_id, day
        ), deltas AS (
            SELECT day,
                   greatest(mono - lag(mono) OVER w, 0) AS mono,
                   greatest(color - lag(color) OVER w, 0) AS color
            FROM daily
            WINDOW w AS (PARTITION BY device_id ORDER BY day)
        )
        SELECT day, coalesce(sum(mono), 0)::bigint, coalesce(sum(color), 0)::bigint
        FROM deltas GROUP BY day ORDER BY day
        """  # noqa: S608 - só fragmentos fixos; valores vão por parâmetro
    )
    params: dict[str, object] = {"tz": DISPLAY_TZ, "since": since, "reseller": p.reseller_id}
    if p.customer_id is not None:
        params["customer"] = p.customer_id
    rows = (await session.execute(sql, params)).all()
    first_day = (datetime.now(UTC) - timedelta(days=days - 1)).astimezone(ZoneInfo(DISPLAY_TZ)).date()
    by_day = {r[0]: (int(r[1]), int(r[2])) for r in rows}
    out = []
    for i in range(days):
        day = first_day + timedelta(days=i)
        mono, color = by_day.get(day, (0, 0))
        out.append(PagesPerDay(day=day, mono=mono, color=color))
    return out
