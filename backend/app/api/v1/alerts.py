"""/api/v1 Alertas (PROMPT 10 item 8): alerts, rules, notification channels, notification log and
settings; printer alerts (16.4) and supply replacements (16.3)."""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.core.errors import not_found
from app.models import Notification
from app.schemas.alerts import (
    AlertActionIn,
    AlertActionOut,
    AlertCounts,
    AlertOut,
    AlertPage,
    AlertRuleIn,
    AlertRuleOut,
    AlertRuleUpdate,
    ChannelIn,
    ChannelOut,
    ChannelTestOut,
    ChannelUpdate,
    NotificationOut,
    NotificationPage,
    NotificationSettings,
    PrinterAlertCounts,
    PrinterAlertPage,
    SupplyReplacementPage,
)
from app.schemas.common import ERROR_RESPONSES
from app.services import alert_rules as rules_svc
from app.services import alerts_portal as svc
from app.services import notifications as notif_svc
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Direction, SortOption, paginate

router = APIRouter(responses=ERROR_RESPONSES, tags=["alertas"])
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Cliente HTTP dos notificadores (webhook/WhatsApp); os testes substituem por um transporte falso."""
    async with httpx.AsyncClient() as client:
        yield client


HttpDep = Annotated[httpx.AsyncClient, Depends(http_client)]


# ----------------------------------------------------------------------------- alertas


@router.get("/alerts", response_model=AlertPage, summary="Alertas com filtros (mais recentes primeiro)")
async def list_alerts(
    p: PrincipalDep,
    session: SessionDep,
    state: Literal["open", "acknowledged", "resolved", "all"] = "open",
    severity: Literal["info", "warning", "critical"] | None = None,
    type: str | None = None,  # noqa: A002 - nome do filtro na API
    customer_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    q: str | None = None,
    sort: str = "opened_at",
    direction: Direction = "desc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> AlertPage:
    f = svc.AlertFilters(
        state=state,
        severity=severity,
        type=type,
        customer_id=customer_id,
        device_id=device_id,
        agent_id=agent_id,
        q=q,
    )
    page, total = await svc.list_alerts(
        session, p, f, sort=sort, direction=direction, limit=limit, cursor=cursor
    )
    return AlertPage(items=await svc.enrich(session, page.items), next_cursor=page.next_cursor, total=total)


@router.get("/alerts/counts", response_model=AlertCounts, summary="Alertas não resolvidos por gravidade")
async def alert_counts(p: PrincipalDep, session: SessionDep) -> AlertCounts:
    return await svc.counts(session, p)


@router.get("/alerts/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> AlertOut:
    out = await svc.get_alert(session, p, alert_id)
    if out is None:
        raise not_found("Alerta")
    return out


@router.post("/alerts/act", response_model=AlertActionOut, summary="Reconhecer ou resolver (em lote)")
async def act(body: AlertActionIn, p: PrincipalDep, session: SessionDep) -> AlertActionOut:
    out = await svc.act(session, p, body)
    await session.commit()
    return out


# ----------------------------------------------------------------------------- regras


@router.get("/alert-rules", response_model=list[AlertRuleOut], summary="Regras (da revenda e por cliente)")
async def list_rules(
    p: PrincipalDep, session: SessionDep, customer_id: uuid.UUID | None = None
) -> list[AlertRuleOut]:
    return await rules_svc.list_rules(session, p, customer_id)


@router.post("/alert-rules", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(body: AlertRuleIn, p: PrincipalDep, session: SessionDep) -> AlertRuleOut:
    out = await rules_svc.create_rule(session, p, body)
    await session.commit()
    return out


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleOut)
async def update_rule(
    rule_id: uuid.UUID, body: AlertRuleUpdate, p: PrincipalDep, session: SessionDep
) -> AlertRuleOut:
    out = await rules_svc.update_rule(session, p, rule_id, body)
    await session.commit()
    return out


@router.delete("/alert-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await rules_svc.delete_rule(session, p, rule_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- canais


@router.get("/notification-channels", response_model=list[ChannelOut])
async def list_channels(p: PrincipalDep, session: SessionDep, settings: SettingsDep) -> list[ChannelOut]:
    return await notif_svc.list_channels(session, settings, p)


@router.post("/notification-channels", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: ChannelIn, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> ChannelOut:
    out = await notif_svc.create_channel(session, settings, p, body)
    await session.commit()
    return out


@router.patch("/notification-channels/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: uuid.UUID, body: ChannelUpdate, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> ChannelOut:
    out = await notif_svc.update_channel(session, settings, p, channel_id, body)
    await session.commit()
    return out


@router.delete("/notification-channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await notif_svc.delete_channel(session, p, channel_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/notification-channels/{channel_id}/test",
    response_model=ChannelTestOut,
    summary="Enviar mensagem de teste agora para todos os destinos do canal",
)
async def test_channel(
    channel_id: uuid.UUID, p: PrincipalDep, session: SessionDep, settings: SettingsDep, client: HttpDep
) -> ChannelTestOut:
    out = await notif_svc.test_channel(session, settings, p, channel_id, client)
    await session.commit()
    return out


# ----------------------------------------------------------------------------- notificações


NOTIFICATION_SORTS = {"created_at": SortOption(Notification.created_at, "datetime")}


@router.get("/notifications", response_model=NotificationPage, summary="Registro de envios")
async def list_notifications(
    p: PrincipalDep,
    session: SessionDep,
    status_: Annotated[
        Literal["pending", "sent", "failed", "suppressed"] | None, Query(alias="status")
    ] = None,
    alert_id: uuid.UUID | None = None,
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> NotificationPage:
    p.require("alerts.read")
    stmt = select(Notification).where(Notification.reseller_id == p.reseller_id)
    if status_:
        stmt = stmt.where(Notification.status == status_)
    if alert_id:
        stmt = stmt.where(Notification.alert_id == alert_id)
    page = await paginate(
        session,
        stmt,
        id_column=Notification.id,
        sort_options=NOTIFICATION_SORTS,
        sort="created_at",
        direction="desc",
        limit=limit,
        cursor=cursor,
    )
    return NotificationPage(
        items=[NotificationOut.model_validate(n) for n in page.items], next_cursor=page.next_cursor
    )


@router.post("/notifications/{notification_id}/retry", response_model=NotificationOut)
async def retry_notification(
    notification_id: uuid.UUID, p: PrincipalDep, session: SessionDep
) -> NotificationOut:
    n = await notif_svc.retry_notification(session, p, notification_id)
    await session.commit()
    return NotificationOut.model_validate(n)


@router.get("/notification-settings", response_model=NotificationSettings)
async def get_notification_settings(p: PrincipalDep, session: SessionDep) -> NotificationSettings:
    p.require("alerts.read")
    return await notif_svc.get_settings(session, p.reseller_id)


@router.put("/notification-settings", response_model=NotificationSettings)
async def put_notification_settings(
    body: NotificationSettings, p: PrincipalDep, session: SessionDep
) -> NotificationSettings:
    out = await notif_svc.put_settings(session, p, body)
    await session.commit()
    return out


# ----------------------------------------------------------------------------- impressora e suprimentos


@router.get(
    "/printer-alerts", response_model=PrinterAlertPage, summary="Alertas da impressora (prtAlertTable)"
)
async def printer_alerts(
    p: PrincipalDep,
    session: SessionDep,
    category: Literal["parts", "service_call", "jam", "consumable", "other"] | None = None,
    active: bool | None = None,
    customer_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    q: str | None = None,
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> PrinterAlertPage:
    items, nxt, total = await svc.list_printer_alerts(
        session,
        p,
        category=category,
        active=active,
        customer_id=customer_id,
        device_id=device_id,
        q=q,
        limit=limit,
        cursor=cursor,
    )
    return PrinterAlertPage(items=items, next_cursor=nxt, total=total)


@router.get("/printer-alerts/counts", response_model=PrinterAlertCounts, summary="Ativos por categoria")
async def printer_alert_counts(p: PrincipalDep, session: SessionDep) -> PrinterAlertCounts:
    return await svc.printer_alert_counts(session, p)


@router.get("/supply-replacements", response_model=SupplyReplacementPage, summary="Trocas de suprimento")
async def supply_replacements(
    p: PrincipalDep,
    session: SessionDep,
    customer_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    color: str | None = None,
    premature: bool | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    q: str | None = None,
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> SupplyReplacementPage:
    items, nxt, total = await svc.list_replacements(
        session,
        p,
        customer_id=customer_id,
        device_id=device_id,
        color=color,
        premature=premature,
        date_from=date_from,
        date_to=date_to,
        q=q,
        limit=limit,
        cursor=cursor,
    )
    return SupplyReplacementPage(items=items, next_cursor=nxt, total=total)
