"""Report engine (PROMPT 10.9 and 16.12): every report is a function that turns the filters into typed
columns + rows (+ optional totals and chart). The same result is shown as HTML (paginated), and exported
as CSV/XLSX/PDF with exactly the same filters."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import bad_request, not_found
from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import Customer, Device

DISPLAY_TZ = ZoneInfo("America/Sao_Paulo")
MAX_PERIOD_DAYS = 400

ColumnKind = Literal["text", "int", "decimal", "money", "percent", "datetime", "date", "bool"]


@dataclass(frozen=True)
class Col:
    key: str
    label: str
    kind: ColumnKind = "text"
    # Coluna que identifica o bloco (equipamento): na tela e no PDF vai no cabeçalho do bloco; no CSV/XLSX
    # continua sendo uma coluna comum.
    section: bool = False


@dataclass(frozen=True)
class Chart:
    kind: Literal["bar", "line"]
    x: str
    series: list[tuple[str, str]]  # (chave da coluna, rótulo)
    stacked: bool = False


@dataclass(frozen=True)
class Section:
    """A block of rows (one device): title, details in the header and its own subtotal row."""

    key: str
    title: str
    details: list[tuple[str, str]] = field(default_factory=list)
    totals: dict[str, Any] | None = None
    totals_label: str = "Subtotal"


@dataclass(frozen=True)
class Stat:
    """A big number at the top of the report (screen and PDF)."""

    label: str
    value: Any
    kind: ColumnKind = "int"


SECTION_KEY = "_section"


@dataclass
class ReportData:
    columns: list[Col]
    rows: list[dict[str, Any]]
    totals: dict[str, Any] | None = None
    chart: Chart | None = None
    notes: list[str] = field(default_factory=list)
    # Linhas em blocos: cada linha traz a chave do bloco em SECTION_KEY, na ordem dos blocos.
    sections: dict[str, Section] | None = None
    summary: list[Stat] = field(default_factory=list)
    chart_rows: list[dict[str, Any]] | None = None  # quando o gráfico não usa as próprias linhas
    # CSV/XLSX num formato próprio (ex.: uma linha por equipamento); `export_groups` = faixas de títulos
    # sobre as colunas (rótulo, quantidade de colunas), em ordem.
    export_columns: list[Col] | None = None
    export_rows: list[dict[str, Any]] | None = None
    export_totals: dict[str, Any] | None = None
    export_groups: list[tuple[str, int]] | None = None


def need[T](value: T | None, what: str) -> T:
    """A filter that `normalize` always fills for this report; missing means a bug, never user input."""
    if value is None:
        raise RuntimeError(f"filtro {what} não foi normalizado")
    return value


@dataclass(frozen=True)
class Filters:
    date_from: date | None
    date_to: date | None
    customer_id: uuid.UUID | None
    site_id: uuid.UUID | None
    date_type: str | None
    group_by: str | None
    month: str | None  # AAAA-MM (cobrança)
    hours: int | None  # sem leitura há N horas

    @property
    def lo(self) -> datetime:
        """Start of the period in UTC (00:00 in São Paulo)."""
        return datetime.combine(need(self.date_from, "date_from"), time.min, DISPLAY_TZ).astimezone(UTC)

    @property
    def hi(self) -> datetime:
        """End of the period in UTC, exclusive (00:00 of the next day in São Paulo)."""
        return datetime.combine(
            need(self.date_to, "date_to") + timedelta(days=1), time.min, DISPLAY_TZ
        ).astimezone(UTC)


@dataclass(frozen=True)
class Option:
    value: str
    label: str


Runner = Callable[[AsyncSession, Principal, Filters], Awaitable[ReportData]]


@dataclass(frozen=True)
class ReportDef:
    key: str
    title: str
    group: str
    description: str
    run: Runner
    period: bool = True  # usa o filtro de período
    cutoff_date: bool = False  # usa uma data de corte (date_to)
    date_types: tuple[Option, ...] = ()
    group_by: tuple[Option, ...] = ()
    month: bool = False
    hours: bool = False
    default_days: int = 30
    group_label: str = "Agrupar"
    max_days: int = MAX_PERIOD_DAYS


REGISTRY: dict[str, ReportDef] = {}


def register(defn: ReportDef) -> ReportDef:
    if defn.key in REGISTRY:
        raise RuntimeError(f"relatório duplicado: {defn.key}")
    REGISTRY[defn.key] = defn
    return defn


def get(key: str) -> ReportDef:
    defn = REGISTRY.get(key)
    if defn is None:
        raise not_found("Relatório")
    return defn


def today_sp() -> date:
    return datetime.now(DISPLAY_TZ).date()


def normalize(defn: ReportDef, f: Filters) -> Filters:
    """Fills defaults and validates the period / options of this report."""
    date_from, date_to = f.date_from, f.date_to
    if defn.period:
        date_to = date_to or today_sp()
        date_from = date_from or date_to - timedelta(days=defn.default_days - 1)
        if date_from > date_to:
            raise bad_request("invalid_period", "A data inicial é depois da final")
        if (date_to - date_from).days + 1 > defn.max_days:
            raise bad_request("period_too_long", f"Período máximo deste relatório: {defn.max_days} dias")
    elif defn.cutoff_date:
        date_from, date_to = None, date_to or today_sp()
    else:
        date_from = date_to = None
    date_type = f.date_type
    if defn.date_types:
        allowed = {o.value for o in defn.date_types}
        date_type = date_type or defn.date_types[0].value
        if date_type not in allowed:
            raise bad_request("invalid_date_type", "Tipo de data inválido para este relatório")
    group_by = f.group_by
    if defn.group_by:
        group_by = group_by or defn.group_by[0].value
        if group_by not in {o.value for o in defn.group_by}:
            raise bad_request("invalid_group_by", "Agrupamento inválido para este relatório")
    month = f.month
    if defn.month:
        month = month or (today_sp().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        parse_month(month)
    hours = f.hours
    if defn.hours:
        hours = hours or 24
    return Filters(date_from, date_to, f.customer_id, f.site_id, date_type, group_by, month, hours)


def parse_month(value: str) -> tuple[datetime, datetime]:
    """'AAAA-MM' → [first day 00:00 SP, first day of the next month 00:00 SP) in UTC."""
    try:
        first = datetime.strptime(value + "-01", "%Y-%m-%d").date()
    except ValueError as exc:
        raise bad_request("invalid_month", "Mês no formato AAAA-MM") from exc
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (
        datetime.combine(first, time.min, DISPLAY_TZ).astimezone(UTC),
        datetime.combine(nxt, time.min, DISPLAY_TZ).astimezone(UTC),
    )


async def check_customer(session: AsyncSession, p: Principal, f: Filters) -> None:
    if f.customer_id is None:
        return
    customer = await session.get(Customer, f.customer_id)
    if (
        customer is None
        or customer.deleted_at is not None
        or not p.can_access_customer(customer.reseller_id, customer.id)
    ):
        raise not_found("Cliente")


def device_scope(p: Principal, f: Filters, *, approved: bool = True) -> ColumnElement[bool]:
    """Devices the user may see, with the customer/site filters; pending/discarded devices stay out of
    every report except Descobertas (16.1)."""
    return and_(
        Device.deleted_at.is_(None),
        Device.discovery_state == "approved" if approved else true(),
        reseller_scope(p, Device.reseller_id),
        customer_scope(p, Device.customer_id),
        Device.customer_id == f.customer_id if f.customer_id else true(),
        Device.site_id == f.site_id if f.site_id else true(),
    )


def sql_scope(p: Principal, f: Filters, alias: str = "d") -> tuple[str, dict[str, Any]]:
    """The same device scope as a SQL fragment (for the text() queries with window functions)."""
    parts = [f"{alias}.deleted_at IS NULL", f"{alias}.discovery_state = 'approved'"]
    params: dict[str, Any] = {}
    if not p.is_superadmin:
        parts.append(f"{alias}.reseller_id = :scope_reseller")
        params["scope_reseller"] = p.reseller_id
    if p.customer_id is not None:
        params["scope_customer"] = p.customer_id
    if f.customer_id is not None:
        params["f_customer"] = f.customer_id
    if f.site_id is not None:
        params["f_site"] = f.site_id
    if history := assignment_conditions(params, "da"):
        # Equipamento que esteve com o cliente/local em algum momento (histórico de transferências);
        # sem histórico gravado, vale o cliente/local atual.
        was = " AND ".join(history)
        now = " AND ".join(f"{alias}.{col} = :{key}" for key, col in HISTORY_KEYS.items() if key in params)
        parts.append(
            f"(EXISTS (SELECT 1 FROM device_assignments da WHERE da.device_id = {alias}.id AND {was})"  # noqa: S608 - fragmentos fixos; valores por parâmetro
            f" OR (NOT EXISTS (SELECT 1 FROM device_assignments dz WHERE dz.device_id = {alias}.id)"
            f" AND {now}))"
        )
    return " AND ".join(parts), params


# Parâmetro do filtro → coluna do vínculo (device_assignments) e do equipamento.
HISTORY_KEYS = {
    "scope_customer": "customer_id",
    "f_customer": "customer_id",
    "erp_customer": "customer_id",
    "f_site": "site_id",
}


def assignment_conditions(params: dict[str, Any], alias: str = "dw") -> list[str]:
    """Conditions on the assignment `alias` for the customer/site filters present in `params`."""
    return [f"{alias}.{col} = :{key}" for key, col in HISTORY_KEYS.items() if key in params]


def reading_window(params: dict[str, Any], alias: str = "r") -> str:
    """With a customer/site filter, only readings taken while the device was with that customer/site count
    (`alias` is the readings alias). Devices without recorded history keep all their readings."""
    conds = assignment_conditions(params)
    if not conds:
        return ""
    was = " AND ".join(conds)
    return (
        f" AND (EXISTS (SELECT 1 FROM device_assignments dw WHERE dw.device_id = {alias}.device_id"  # noqa: S608 - fragmentos fixos; valores por parâmetro
        f" AND {was} AND {alias}.read_at >= dw.start_at"
        f" AND (dw.end_at IS NULL OR {alias}.read_at < dw.end_at))"
        f" OR NOT EXISTS (SELECT 1 FROM device_assignments dy WHERE dy.device_id = {alias}.device_id))"
    )
