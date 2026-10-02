"""Mapa dos locais (PROMPT 16.9 / 10.4): every site with its coordinates and a status built from the
collectors, the devices and the open alerts."""

import uuid

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import Agent, Alert, Customer, Device, Site
from app.schemas.tenancy import SiteMapItem

ONLINE_STATES = ("online", "degraded", "paused")


async def site_map(session: AsyncSession, p: Principal, customer_id: uuid.UUID | None) -> list[SiteMapItem]:
    p.require("customers.read")
    scope = and_(
        Site.deleted_at.is_(None),
        reseller_scope(p, Site.reseller_id),
        customer_scope(p, Site.customer_id),
        Site.customer_id == customer_id if customer_id else true(),
    )
    devices = (
        select(
            Device.site_id.label("site_id"),
            func.count().label("devices"),
            func.count()
            .filter(or_(Device.disconnected.is_(True), Device.last_status == "offline"))
            .label("offline"),
        )
        .where(Device.deleted_at.is_(None), Device.active.is_(True), Device.discovery_state == "approved")
        .group_by(Device.site_id)
        .subquery()
    )
    agents = (
        select(
            Agent.site_id.label("site_id"),
            func.count().label("agents"),
            func.count().filter(Agent.state.in_(ONLINE_STATES)).label("online"),
        )
        .where(Agent.deleted_at.is_(None), Agent.revoked_at.is_(None), Agent.enrolled_at.is_not(None))
        .group_by(Agent.site_id)
        .subquery()
    )
    alerts = (
        select(Alert.site_id.label("site_id"), func.count().label("alerts"))
        .where(Alert.state != "resolved", Alert.site_id.is_not(None))
        .group_by(Alert.site_id)
        .subquery()
    )
    rows = await session.execute(
        select(
            Site,
            Customer.name,
            func.coalesce(devices.c.devices, 0),
            func.coalesce(devices.c.offline, 0),
            func.coalesce(agents.c.agents, 0),
            func.coalesce(agents.c.online, 0),
            func.coalesce(alerts.c.alerts, 0),
        )
        .join(Customer, Customer.id == Site.customer_id)
        .outerjoin(devices, devices.c.site_id == Site.id)
        .outerjoin(agents, agents.c.site_id == Site.id)
        .outerjoin(alerts, alerts.c.site_id == Site.id)
        .where(scope, Customer.deleted_at.is_(None))
        .order_by(Customer.name, Site.name)
    )
    out = []
    for site, cname, n_dev, n_off, n_agents, n_online, n_alerts in rows.tuples():
        if n_agents == 0:
            status = "no_agent"
        elif n_online == 0:
            status = "offline"
        elif n_off or n_alerts:
            status = "warning"
        else:
            status = "ok"
        out.append(
            SiteMapItem(
                id=site.id,
                name=site.name,
                customer_id=site.customer_id,
                customer_name=cname,
                city=site.city,
                state=site.state,
                latitude=site.latitude,
                longitude=site.longitude,
                status=status,
                devices=n_dev,
                devices_offline=n_off,
                agents=n_agents,
                agents_online=n_online,
                alerts_open=n_alerts,
            )
        )
    return out
