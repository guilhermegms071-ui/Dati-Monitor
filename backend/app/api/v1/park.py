"""/api/v1 park screen, device detail, dashboard and live events (Phase 4)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.models import Reading
from app.schemas.common import ERROR_RESPONSES
from app.schemas.park import (
    AdjustmentIn,
    AdjustmentOut,
    BulkDevicesIn,
    BulkDevicesOut,
    CounterPoint,
    Dashboard,
    DeviceUpdate,
    ParkCounts,
    ParkPage,
    ParkRow,
    SupplyPoint,
)
from app.services import dashboard as dashboard_svc
from app.services import devices as devices_svc
from app.services import live
from app.services import park as svc
from app.services.export import ExportColumn, ExportFormat, export_response
from app.services.labels import DEVICE_STATUS_LABELS
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Direction

router = APIRouter(responses=ERROR_RESPONSES)
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
Text = Annotated[str | None, Query(max_length=200)]

STATUS_LABELS = DEVICE_STATUS_LABELS


def _filters(
    q: str | None,
    status: list[str] | None,
    serial: str | None,
    ip: str | None,
    brand: str | None,
    model: str | None,
    sector: str | None,
    asset_tag: str | None,
    customer_id: uuid.UUID | None,
    site_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    disconnected: bool,
    inactive: bool,
) -> svc.ParkFilters:
    return svc.ParkFilters(
        q=q,
        status=tuple(status or ()),
        serial=serial,
        ip=ip,
        brand=brand,
        model=model,
        sector=sector,
        asset_tag=asset_tag,
        customer_id=customer_id,
        site_id=site_id,
        agent_id=agent_id,
        disconnected=disconnected,
        inactive=inactive,
    )


@router.get(
    "/park", response_model=ParkPage, tags=["equipamentos"], summary="Tela de parque (filtros por coluna)"
)
async def park(
    p: PrincipalDep,
    session: SessionDep,
    q: Text = None,
    status: Annotated[list[str] | None, Query()] = None,
    serial: Text = None,
    ip: Text = None,
    brand: Text = None,
    model: Text = None,
    sector: Text = None,
    asset_tag: Text = None,
    customer_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    disconnected: bool = False,
    inactive: bool = False,
    sort: str = "serial",
    direction: Direction = "asc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> ParkPage:
    f = _filters(
        q,
        status,
        serial,
        ip,
        brand,
        model,
        sector,
        asset_tag,
        customer_id,
        site_id,
        agent_id,
        disconnected,
        inactive,
    )
    page, total = await svc.list_park(
        session, p, f, sort=sort, direction=direction, limit=limit, cursor=cursor
    )
    return ParkPage(items=await svc.enrich(session, page.items), next_cursor=page.next_cursor, total=total)


@router.get("/park/counts", response_model=ParkCounts, tags=["equipamentos"])
async def park_counts(p: PrincipalDep, session: SessionDep) -> ParkCounts:
    return await svc.counts(session, p)


def _levels(r: ParkRow) -> str:
    names = {"cyan": "C", "magenta": "M", "yellow": "Y", "black": "K"}
    return " ".join(
        f"{names.get(s.color, s.color)}:{'n/d' if s.percent is None else f'{s.percent:.0f}%'}"
        for s in r.supplies
    )


PARK_COLUMNS: list[ExportColumn[ParkRow]] = [
    ExportColumn("Status", lambda r: STATUS_LABELS.get(r.last_status, r.last_status)),
    ExportColumn("IP", lambda r: r.ip),
    ExportColumn("DCA (coletor)", lambda r: r.agent_name),
    ExportColumn("Descoberta", lambda r: r.first_seen_at),
    ExportColumn("Comunicação", lambda r: r.last_read_at),
    ExportColumn("PAT", lambda r: r.asset_tag),
    ExportColumn("Serial", lambda r: r.serial),
    ExportColumn("Marca", lambda r: r.brand),
    ExportColumn("Modelo", lambda r: r.model),
    ExportColumn("Setor", lambda r: r.sector),
    ExportColumn("Cliente", lambda r: r.customer_name),
    ExportColumn("Local", lambda r: r.site_name),
    ExportColumn("Total", lambda r: r.last_total),
    ExportColumn("PB", lambda r: r.last_mono),
    ExportColumn("Cor", lambda r: r.last_color),
    ExportColumn("Monitorado", lambda r: r.monitored),
    ExportColumn("Desconectado", lambda r: r.disconnected),
    ExportColumn("Níveis", _levels),
]


@router.get("/park/export", tags=["equipamentos"], summary="Exportar o parque com os filtros aplicados")
async def park_export(
    p: PrincipalDep,
    session: SessionDep,
    format: ExportFormat = "xlsx",  # noqa: A002 - nome do parâmetro na URL
    q: Text = None,
    status: Annotated[list[str] | None, Query()] = None,
    serial: Text = None,
    ip: Text = None,
    brand: Text = None,
    model: Text = None,
    sector: Text = None,
    asset_tag: Text = None,
    customer_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    disconnected: bool = False,
    inactive: bool = False,
    sort: str = "serial",
    direction: Direction = "asc",
) -> Response:
    f = _filters(
        q,
        status,
        serial,
        ip,
        brand,
        model,
        sector,
        asset_tag,
        customer_id,
        site_id,
        agent_id,
        disconnected,
        inactive,
    )
    rows = await svc.export_rows(session, p, f, sort, direction)
    return export_response(rows, PARK_COLUMNS, fmt=format, basename="parque")


@router.post("/devices/bulk", response_model=BulkDevicesOut, tags=["equipamentos"], summary="Ações em massa")
async def devices_bulk(
    body: BulkDevicesIn, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> BulkDevicesOut:
    out = await svc.bulk(session, settings, p, body)
    await session.commit()
    return out


@router.patch("/devices/{device_id}", response_model=ParkRow, tags=["equipamentos"])
async def update_device(
    device_id: uuid.UUID, body: DeviceUpdate, p: PrincipalDep, session: SessionDep
) -> ParkRow:
    device = await svc.update_device(session, p, device_id, body)
    await session.commit()
    return (await svc.enrich(session, [device]))[0]


@router.get(
    "/devices/{device_id}/row", response_model=ParkRow, tags=["equipamentos"], summary="Linha do parque"
)
async def device_row(device_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> ParkRow:
    return (await svc.enrich(session, [await devices_svc.get_device(session, p, device_id)]))[0]


@router.get("/devices/{device_id}/counters", response_model=list[CounterPoint], tags=["equipamentos"])
async def device_counters(
    device_id: uuid.UUID,
    p: PrincipalDep,
    session: SessionDep,
    granularity: Literal["day", "month"] = "day",
    periods: Annotated[int, Query(ge=1, le=366)] = 30,
) -> list[CounterPoint]:
    return await svc.counter_series(session, p, device_id, granularity=granularity, periods=periods)


@router.get("/devices/{device_id}/supplies/history", response_model=list[SupplyPoint], tags=["equipamentos"])
async def device_supply_history(
    device_id: uuid.UUID, p: PrincipalDep, session: SessionDep, days: Annotated[int, Query(ge=1, le=400)] = 30
) -> list[SupplyPoint]:
    return await svc.supply_history(session, p, device_id, days)


@router.get("/devices/{device_id}/adjustments", response_model=list[AdjustmentOut], tags=["equipamentos"])
async def list_adjustments(device_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> list[AdjustmentOut]:
    return await svc.list_adjustments(session, p, device_id)


@router.post(
    "/devices/{device_id}/adjustments",
    response_model=list[AdjustmentOut],
    tags=["equipamentos"],
    status_code=201,
    summary="Ajuste manual de leitura (com motivo)",
)
async def add_adjustment(
    device_id: uuid.UUID, body: AdjustmentIn, p: PrincipalDep, session: SessionDep
) -> list[AdjustmentOut]:
    await svc.add_adjustment(session, p, device_id, body)
    await session.commit()
    return await svc.list_adjustments(session, p, device_id)


READING_COLUMNS: list[ExportColumn[Reading]] = [
    ExportColumn("Data da leitura", lambda r: r.read_at),
    ExportColumn("Recebida em", lambda r: r.received_at),
    ExportColumn("Total", lambda r: r.total),
    ExportColumn("PB", lambda r: r.mono),
    ExportColumn("Cor", lambda r: r.color),
    ExportColumn("Cópia PB", lambda r: r.copy_mono),
    ExportColumn("Cópia cor", lambda r: r.copy_color),
    ExportColumn("Impressão PB", lambda r: r.print_mono),
    ExportColumn("Impressão cor", lambda r: r.print_color),
    ExportColumn("Digitalização", lambda r: r.scan),
    ExportColumn("Status", lambda r: STATUS_LABELS.get(r.status or "", r.status)),
    ExportColumn("Origem", lambda r: r.source),
    ExportColumn("Fonte dos contadores", lambda r: r.counter_source),
    ExportColumn("Alertas", lambda r: r.flags),
]


@router.get("/devices/{device_id}/readings/export", tags=["equipamentos"], summary="Exportar leituras")
async def export_readings(
    device_id: uuid.UUID,
    p: PrincipalDep,
    session: SessionDep,
    format: ExportFormat = "xlsx",  # noqa: A002 - nome do parâmetro na URL
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Response:
    rows = await svc.readings_for_export(session, p, device_id, date_from, date_to)
    return export_response(rows, READING_COLUMNS, fmt=format, basename="leituras")


@router.get("/dashboard", response_model=Dashboard, tags=["painel"])
async def dashboard(
    p: PrincipalDep, session: SessionDep, days: Annotated[int, Query(ge=7, le=90)] = 30
) -> Dashboard:
    return await dashboard_svc.build(session, p, days)


@router.get(
    "/events",
    tags=["painel"],
    summary="Eventos ao vivo (Server-Sent Events): coletores, comandos e novas leituras",
    response_class=StreamingResponse,
)
async def events(request: Request, p: PrincipalDep) -> StreamingResponse:
    broker: live.LiveBroker = request.app.state.live
    return StreamingResponse(
        live.stream(broker, p, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
