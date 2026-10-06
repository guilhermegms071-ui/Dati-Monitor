"""/api/v1 Relatórios (PROMPT 10.9 / 16.12): catalog, the report on screen (paginated) and the same report
as CSV / XLSX / PDF with the same filters."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import PrincipalDep, SessionDep
from app.core.principal import Principal
from app.models import Customer, Site
from app.schemas.common import ERROR_RESPONSES
from app.schemas.reports import (
    ReportAppliedFilters,
    ReportChart,
    ReportColumn,
    ReportInfo,
    ReportOption,
    ReportResult,
    ReportSection,
    ReportSectionDetail,
    ReportStat,
)
from app.services import audit
from app.services import reports as svc
from app.services.export import DISPLAY_TZ
from app.services.reports.render import MEDIA, json_cell, render

router = APIRouter(responses=ERROR_RESPONSES, tags=["relatórios"])
MAX_SCREEN_ROWS = 500
CNPJ_DIGITS = 14


@dataclass(frozen=True)
class FilterParams:
    date_from: Annotated[date | None, Query(description="Início do período (dia, horário de Brasília)")] = (
        None
    )
    date_to: Annotated[date | None, Query(description="Fim do período, inclusive; ou a data de corte")] = None
    customer_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    date_type: Annotated[str | None, Query(max_length=32, description="Tipo de data (ver catálogo)")] = None
    group_by: Annotated[str | None, Query(max_length=32)] = None
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$", description="Mês da cobrança (AAAA-MM)")] = (
        None
    )
    hours: Annotated[int | None, Query(ge=1, le=24 * 90)] = None

    def to_filters(self) -> svc.Filters:
        return svc.Filters(
            self.date_from,
            self.date_to,
            self.customer_id,
            self.site_id,
            self.date_type,
            self.group_by,
            self.month,
            self.hours,
        )


FilterDep = Annotated[FilterParams, Depends()]


def _info(d: svc.ReportDef) -> ReportInfo:
    return ReportInfo(
        key=d.key,
        title=d.title,
        group=d.group,
        description=d.description,
        period=d.period,
        cutoff_date=d.cutoff_date,
        month=d.month,
        hours=d.hours,
        default_days=d.default_days,
        date_types=[ReportOption(value=o.value, label=o.label) for o in d.date_types],
        group_by=[ReportOption(value=o.value, label=o.label) for o in d.group_by],
        group_label=d.group_label,
    )


@router.get("/reports", response_model=list[ReportInfo], summary="Catálogo de relatórios")
async def list_reports(p: PrincipalDep) -> list[ReportInfo]:
    p.require("reports.read")
    return [_info(d) for d in svc.REGISTRY.values()]


async def _run(
    key: str, p: Principal, session: SessionDep, params: FilterParams
) -> tuple[svc.ReportDef, svc.Filters, svc.ReportData]:
    p.require("reports.read")
    defn = svc.get(key)
    f = svc.normalize(defn, params.to_filters())
    return defn, f, await defn.run(session, p, f)


def _row(row: dict[str, object], keys: list[str]) -> dict[str, object]:
    return {k: json_cell(row.get(k)) for k in keys}


@router.get("/reports/{key}", response_model=ReportResult, summary="Relatório na tela (paginado)")
async def run_report(
    key: str,
    p: PrincipalDep,
    session: SessionDep,
    params: FilterDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_SCREEN_ROWS)] = 100,
) -> ReportResult:
    defn, f, data = await _run(key, p, session, params)
    keys = [c.key for c in data.columns]
    if data.sections is not None:
        keys.append(svc.SECTION_KEY)
    chart_keys = [data.chart.x, *(k for k, _ in data.chart.series)] if data.chart else []
    page = data.rows[offset : offset + limit]
    chart_source = data.chart_rows if data.chart_rows is not None else data.rows
    in_page = list(dict.fromkeys(str(r.get(svc.SECTION_KEY)) for r in page)) if data.sections else []
    return ReportResult(
        key=defn.key,
        title=defn.title,
        filters=ReportAppliedFilters(
            date_from=f.date_from,
            date_to=f.date_to,
            date_type=f.date_type,
            group_by=f.group_by,
            month=f.month,
            hours=f.hours,
        ),
        columns=[
            ReportColumn(key=c.key, label=c.label, kind=c.kind, section=c.section) for c in data.columns
        ],
        rows=[_row(r, keys) for r in page],
        total_rows=len(data.rows),
        offset=offset,
        limit=limit,
        totals=_row(data.totals, keys) if data.totals else None,
        chart=ReportChart(
            kind=data.chart.kind,
            x=data.chart.x,
            series=[ReportOption(value=k, label=label) for k, label in data.chart.series],
            stacked=data.chart.stacked,
        )
        if data.chart
        else None,
        chart_rows=[_row(r, chart_keys) for r in chart_source] if data.chart else None,
        notes=data.notes,
        sections=[_section(data.sections[k], keys) for k in in_page if data.sections and k in data.sections],
        summary=[ReportStat(label=s.label, value=json_cell(s.value), kind=s.kind) for s in data.summary],
    )


def _section(s: svc.Section, keys: list[str]) -> ReportSection:
    return ReportSection(
        key=s.key,
        title=s.title,
        details=[ReportSectionDetail(label=label, value=value) for label, value in s.details],
        totals=_row(s.totals, keys) if s.totals else None,
        totals_label=s.totals_label,
    )


async def _meta(session: SessionDep, defn: svc.ReportDef, f: svc.Filters) -> list[tuple[str, str]]:
    """Filters shown in the PDF header (the customer/site were already checked by the report)."""
    out: list[tuple[str, str]] = []
    if f.customer_id:
        customer = await session.get(Customer, f.customer_id)
        if customer is not None:
            code = f" (código {customer.erp_code})" if customer.erp_code else ""
            out.append(("Cliente", customer.name + code))
            if customer.cnpj and len(customer.cnpj) == CNPJ_DIGITS:
                c = customer.cnpj
                out.append(("CNPJ", f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"))
    else:
        out.append(("Cliente", "Todos"))
    if f.site_id:
        site = await session.get(Site, f.site_id)
        if site is not None:
            out.append(("Local", site.name))
    if f.date_from and f.date_to:
        out.append(("Período", f"{f.date_from:%d/%m/%Y} a {f.date_to:%d/%m/%Y}"))
    elif f.date_to:
        out.append(("Data de corte", f"{f.date_to:%d/%m/%Y}"))
    if f.month:
        out.append(("Mês", f"{f.month[5:]}/{f.month[:4]}"))
    if f.hours:
        out.append(("Sem leitura há mais de", f"{f.hours} h"))
    for name, options, value in (
        (defn.group_label, defn.group_by, f.group_by),
        ("Tipo de data", defn.date_types, f.date_type),
    ):
        label = next((o.label for o in options if o.value == value), None)
        if label:
            out.append((name, label))
    return out


@router.get(
    "/reports/{key}/export",
    summary="Exportar relatório (CSV, XLSX ou PDF) com os mesmos filtros",
    response_class=Response,
    responses={200: {"content": {m: {} for m in MEDIA.values()}}},
)
async def export_report(
    key: str,
    p: PrincipalDep,
    session: SessionDep,
    params: FilterDep,
    format: Literal["csv", "xlsx", "pdf"] = "xlsx",  # noqa: A002 - nome do parâmetro na URL
) -> Response:
    defn, f, data = await _run(key, p, session, params)
    body = render(data, format, title=defn.title, meta=await _meta(session, defn, f))
    await audit.record(
        session,
        p,
        action="export",
        entity="report",
        entity_id=defn.key,
        reseller_id=p.reseller_id,
        after={"format": format, "rows": len(data.rows), "customer_id": str(f.customer_id or "")},
    )
    await session.commit()
    stamp = datetime.now(DISPLAY_TZ).strftime("%Y%m%d-%H%M")
    return Response(
        content=body,
        media_type=MEDIA[format],
        headers={"Content-Disposition": f'attachment; filename="{defn.key}-{stamp}.{format}"'},
    )
