"""The reports of PROMPT 10.9 and 16.12. Each one returns typed columns and rows; the API renders them as
HTML (paginated), CSV, XLSX and PDF."""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, and_, cast, func, or_, select, text, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import (
    Agent,
    Alert,
    Customer,
    Device,
    DeviceEvent,
    ReadingReview,
    Site,
    SupplyCurrent,
    SupplyReplacement,
)
from app.services.labels import DEVICE_STATUS_LABELS
from app.services.reports import counters
from app.services.reports.base import (
    DISPLAY_TZ,
    SECTION_KEY,
    Chart,
    Col,
    Filters,
    Option,
    ReportData,
    ReportDef,
    Section,
    Stat,
    check_customer,
    device_scope,
    need,
    parse_month,
    register,
    sql_scope,
)

LastAgent = aliased(Agent)
COLOR_LABELS = {"black": "Preto", "cyan": "Ciano", "magenta": "Magenta", "yellow": "Amarelo"}
AGENT_STATE_LABELS = {"online": "Online", "offline": "Offline", "degraded": "Degradado", "paused": "Pausado"}
DISCOVERY_LABELS = {"pending": "Pendente", "approved": "Ativado", "discarded": "Descartado"}
REVIEW_LABELS = {"valid": "Válida", "read_error": "Erro de leitura", "board_replacement": "Troca de placa"}
SEVERITY_LABELS = {"info": "Informação", "warning": "Atenção", "critical": "Crítico"}
ALERT_STATE_LABELS = {"open": "Aberto", "acknowledged": "Reconhecido", "resolved": "Resolvido"}
EVENT_LABELS = {
    "ip_changed": "Troca de IP",
    "replaced": "Equipamento substituído",
    "moved_site": "Mudança de local",
}
# Heartbeats ficam 30 dias (retenção, seção 8): a disponibilidade só pode olhar esse período.
HEARTBEAT_RETENTION_DAYS = 30
AVAILABILITY_BUCKET = timedelta(minutes=5)

C_CUSTOMER = Col("customer", "Cliente")
C_SITE = Col("site", "Local")
C_SERIAL = Col("serial", "Serial")
C_PAT = Col("asset_tag", "PAT")
C_BRAND = Col("brand", "Marca")
C_MODEL = Col("model", "Modelo")
C_SECTOR = Col("sector", "Setor")
C_IP = Col("ip", "IP")
C_AGENT = Col("agent", "Coletor (DCA)")
DEVICE_COLS = [C_CUSTOMER, C_SITE, C_SERIAL, C_PAT, C_BRAND, C_MODEL, C_SECTOR]


def _device_select(p: Principal, f: Filters, *, approved: bool = True) -> Any:
    return (
        select(Device, Customer.name, Customer.erp_code, Site.name, LastAgent.name)
        .join(Customer, Customer.id == Device.customer_id)
        .join(Site, Site.id == Device.site_id)
        .outerjoin(LastAgent, LastAgent.id == Device.last_agent_id)
        .where(device_scope(p, f, approved=approved))
    )


def _dev(d: Device, customer: str, site: str, agent: str | None = None) -> dict[str, Any]:
    return {
        "customer": customer,
        "site": site,
        "serial": d.serial,
        "asset_tag": d.asset_tag,
        "brand": d.brand,
        "model": d.model,
        "sector": d.sector,
        "ip": d.ip,
        "agent": agent,
    }


def _sum(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in keys:
        vals = [r[k] for r in rows if r.get(k) is not None]
        out[k] = sum(vals) if vals else None
    return out


async def _devices(session: AsyncSession, p: Principal, f: Filters, *where: ColumnElement[bool]) -> Any:
    await check_customer(session, p, f)
    order = (Customer.name, Site.name, Device.serial)
    return (await session.execute(_device_select(p, f).where(*where).order_by(*order))).tuples().all()


# ----------------------------------------------------------------------------- produção e cobrança


async def production(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    await check_customer(session, p, f)
    scope, params = sql_scope(p, f)
    prod = await counters.production_by_device(session, scope, params, f.lo, f.hi)
    rows_by_device = []
    for d, cname, _erp, sname, _agent in await _devices(session, p, f):
        pr = prod.get(d.id)
        rows_by_device.append(
            {
                **_dev(d, cname, sname),
                "device_id": d.id,
                "site_id": d.site_id,
                "customer_id": d.customer_id,
                "mono": pr.mono if pr else 0,
                "color": pr.color if pr else 0,
                "total": pr.total if pr else 0,
                "readings": pr.readings if pr else 0,
            }
        )
    numbers = [Col("mono", "PB", "int"), Col("color", "Cor", "int"), Col("total", "Total", "int")]
    notes = ["Leituras com regressão de contador não entram (a não ser que classificadas como válidas)."]
    if f.group_by == "device":
        cols = [*DEVICE_COLS, *numbers, Col("readings", "Leituras", "int")]
        return ReportData(cols, rows_by_device, _sum(rows_by_device, ["mono", "color", "total"]), notes=notes)
    key, cols = ("site_id", [C_CUSTOMER, C_SITE]) if f.group_by == "site" else ("customer_id", [C_CUSTOMER])
    groups: dict[uuid.UUID, dict[str, Any]] = {}
    for r in rows_by_device:
        g = groups.setdefault(
            r[key],
            {"customer": r["customer"], "site": r["site"], "devices": 0, "mono": 0, "color": 0, "total": 0},
        )
        g["devices"] += 1
        for k in ("mono", "color", "total"):
            g[k] += r[k]
    rows = list(groups.values())
    cols = [*cols, Col("devices", "Equipamentos", "int"), *numbers]
    return ReportData(cols, rows, _sum(rows, ["devices", "mono", "color", "total"]), notes=notes)


DAILY_MAX_DAYS = 62  # uma coluna por dia: até dois meses cabem na tela e no PDF


async def daily_counter(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    """Contador diário: uma linha por equipamento, uma coluna por dia com as páginas impressas no dia e o
    total do período; a última linha soma todos os equipamentos de cada dia."""
    scope, params = sql_scope(p, f)
    devices = await _devices(session, p, f)
    daily = await counters.daily_by_device(session, scope, params, f.lo, f.hi)
    days = []
    day = need(f.date_from, "date_from")
    while day <= need(f.date_to, "date_to"):
        days.append(day)
        day += timedelta(days=1)
    day_cols = [Col(f"d{d:%Y%m%d}", f"{d:%d/%m}", "int") for d in days]
    rows = []
    for d, cname, _erp, sname, _agent in devices:
        per = daily.get(d.id, {})
        row: dict[str, Any] = {**_dev(d, cname, sname), "model": _model(d)}
        for dd, col in zip(days, day_cols, strict=True):
            row[col.key] = per[dd].pages_total if dd in per else 0
        row["total"] = sum(row[c.key] for c in day_cols)
        rows.append(row)
    chart_rows = []
    for dd in days:
        mono = sum(v.pages_mono for per in daily.values() if (v := per.get(dd)) is not None)
        color = sum(v.pages_color for per in daily.values() if (v := per.get(dd)) is not None)
        chart_rows.append({"day": dd, "mono": mono, "color": color})
    cols = [
        C_CUSTOMER,
        C_SITE,
        Col("serial", "Nº de série"),
        C_MODEL,
        C_SECTOR,
        *day_cols,
        Col("total", "Total", "int"),
    ]
    chart = Chart("bar", "day", [("mono", "PB"), ("color", "Cor")], stacked=True)
    notes = [
        "Cada coluna é um dia: páginas impressas naquele dia (PB + cor). Dia sem leitura conta 0; as páginas "
        "aparecem no dia da leitura seguinte.",
    ]
    return ReportData(
        cols,
        rows,
        _sum(rows, [*(c.key for c in day_cols), "total"]),
        chart,
        notes,
        chart_rows=chart_rows,
    )


def _model(d: Device) -> str | None:
    """Brand + model without repeating the brand ("Konica Minolta bizhub C287")."""
    if d.brand and d.model and d.model.lower().startswith(d.brand.lower()):
        return d.model
    return " ".join(x for x in (d.brand, d.model) if x) or None


DEVICE_COUNTER_VIEWS = (
    Option("period", "Resumo do período"),
    Option("daily", "Dia a dia"),
    Option("daily_read", "Dia a dia, só dias com leitura"),
)
# Colunas de identificação: no cabeçalho de cada bloco na tela/PDF, colunas comuns no CSV/XLSX.
SECTION_COLS = [
    Col("customer", "Cliente", section=True),
    Col("site", "Local", section=True),
    Col("serial", "Nº de série", section=True),
    Col("model", "Modelo", section=True),
    Col("sector", "Setor", section=True),
]


def _fmt_int(v: int | None) -> str:
    return "—" if v is None else f"{v:,}".replace(",", ".")


def _fmt_dt(v: datetime | None) -> str:
    return v.astimezone(DISPLAY_TZ).strftime("%d/%m/%Y %H:%M") if v else "sem leitura"


def _device_title(d: Device) -> str:
    return f"{_model(d) or 'Equipamento'} · Nº de série {d.serial}"


async def device_counters(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    """Contadores por equipamento (no formato do Datacount): um bloco por equipamento, com a leitura inicial,
    a final e a diferença de cada contador, ou o contador e as páginas de cada dia."""
    await check_customer(session, p, f)
    scope, params = sql_scope(p, f)
    devices = await _devices(session, p, f)
    period = await counters.period_by_device(session, scope, params, f.lo, f.hi)
    daily_view = f.group_by in {"daily", "daily_read"}
    daily = await counters.daily_by_device(session, scope, params, f.lo, f.hi) if daily_view else {}
    rows: list[dict[str, Any]] = []
    sections: dict[str, Section] = {}
    grand = {"pages_mono": 0, "pages_color": 0, "pages_total": 0}
    with_reading = 0
    for d, cname, _erp, sname, _agent in devices:
        pc = period.get(d.id)
        color = d.is_color is not False
        start, end = (pc.start, pc.end) if pc else (None, None)
        pages = {
            "pages_mono": pc.pages_mono if pc else 0,
            "pages_color": (pc.pages_color if pc else 0) if color else None,
            "pages_total": pc.pages_total if pc else 0,
        }
        with_reading += end is not None
        for k in grand:
            grand[k] += pages[k] or 0
        base = {
            SECTION_KEY: str(d.id),
            "customer": cname,
            "site": sname,
            "serial": d.serial,
            "model": _model(d),
            "sector": d.sector,
        }
        details = [
            ("Cliente", cname),
            ("Local", sname),
            ("Setor", d.sector or "—"),
            ("Franquia", _franchise(d)),
        ]
        if daily_view:
            details += [
                ("Contador no início", _fmt_int(start.total if start else None)),
                ("Última leitura", _fmt_dt(end.read_at if end else None)),
            ]
            rows += _daily_rows(
                base, daily.get(d.id, {}), f, color=color, only_read=f.group_by == "daily_read"
            )
            sections[base[SECTION_KEY]] = Section(
                base[SECTION_KEY], _device_title(d), details, pages, "Total do equipamento"
            )
        else:
            details += [
                ("Leitura inicial", _fmt_dt(start.read_at if start else None)),
                ("Leitura final", _fmt_dt(end.read_at if end else None)),
            ]
            rows += [{**base, **line} for line in _period_lines(pc, pages, color=color)]
            sections[base[SECTION_KEY]] = Section(base[SECTION_KEY], _device_title(d), details)
    summary = [
        Stat("Equipamentos", len(devices)),
        Stat("Com leitura no período", with_reading),
        Stat("Páginas PB", grand["pages_mono"]),
        Stat("Páginas coloridas", grand["pages_color"]),
        Stat("Total de páginas", grand["pages_total"]),
    ]
    notes = [
        "Páginas = soma do que cada equipamento imprimiu entre leituras válidas; contador que voltou "
        "(troca de placa, erro de leitura) não entra.",
    ]
    if daily_view:
        if f.group_by == "daily":
            notes.append("Dia sem leitura aparece com — no contador e 0 página.")
        return ReportData(
            [*SECTION_COLS, *DAILY_COLS],
            rows,
            dict(grand),
            Chart("bar", "day", [("mono", "PB"), ("color", "Cor")], stacked=True),
            notes,
            sections=sections,
            summary=summary,
            chart_rows=_per_day(rows, f),
        )
    notes.append(
        "Leitura inicial = última leitura antes do período (base da produção) ou, sem ela, a primeira do "
        "período."
    )
    data = ReportData(
        [*SECTION_COLS, *PERIOD_COLS], rows, None, None, notes, sections=sections, summary=summary
    )
    _period_export(data, devices, period)
    return data


def _franchise(d: Device) -> str:
    parts = []
    if d.franchise_pages_mono is not None:
        parts.append(f"PB {_fmt_int(d.franchise_pages_mono)} pág.")
    if d.franchise_pages_color is not None:
        parts.append(f"cor {_fmt_int(d.franchise_pages_color)} pág.")
    if d.franchise_value is not None:
        parts.append(
            "R$ " + f"{d.franchise_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
    return " · ".join(parts) or "—"


# Excel/CSV do resumo do período no formato do Datacount: uma linha por equipamento, com a primeira
# leitura, a última e a tiragem lado a lado (o PDF e a tela continuam em blocos).
_SNAP_FIELDS: list[tuple[str, str, str | tuple[str, str]]] = [
    ("pb_a4", "PB A4", ("mono", "mono_large")),
    ("pb_a3", "PB A3", "mono_large"),
    ("pb", "Total PB", "mono"),
    ("cor_a4", "Cor A4", ("color", "color_large")),
    ("cor_a3", "Cor A3", "color_large"),
    ("cor", "Total cor", "color"),
    ("total", "Total geral", "total"),
    ("scan", "Digitalizações", "scan"),
]


def _snap_value(s: counters.Snapshot | None, field: str | tuple[str, str]) -> int | None:
    if isinstance(field, tuple):
        return _a4(s, *field)
    return _snap(s, field)


def _period_export(data: ReportData, devices: Any, period: dict[uuid.UUID, counters.PeriodCounters]) -> None:
    ident = [
        Col("serial", "Nº de série"),
        Col("model", "Modelo"),
        Col("sector", "Setor"),
        Col("customer", "Cliente"),
        Col("site", "Local"),
        Col("franchise", "Franquia"),
    ]
    groups: list[tuple[str, list[Col]]] = [("Equipamento", ident)]
    for prefix, label in (("first", "Primeira leitura"), ("last", "Última leitura")):
        groups.append(
            (
                label,
                [
                    Col(f"{prefix}_at", "Data da coleta", "datetime"),
                    *(Col(f"{prefix}_{k}", lb, "int") for k, lb, _ in _SNAP_FIELDS),
                ],
            )
        )
    groups.append(
        ("Tiragem (páginas no período)", [Col(f"diff_{k}", lb, "int") for k, lb, _ in _SNAP_FIELDS])
    )
    rows = []
    for d, cname, _erp, sname, _agent in devices:
        pc = period.get(d.id)
        color = d.is_color is not False
        start, end = (pc.start, pc.end) if pc else (None, None)
        row: dict[str, Any] = {
            "serial": d.serial,
            "model": _model(d),
            "sector": d.sector,
            "customer": cname,
            "site": sname,
            "franchise": _franchise(d),
            "first_at": start.read_at if start else None,
            "last_at": end.read_at if end else None,
        }
        for key, _label, field in _SNAP_FIELDS:
            a, b = _snap_value(start, field), _snap_value(end, field)
            if key.startswith("cor") and not color:
                a = b = None
            row[f"first_{key}"], row[f"last_{key}"] = a, b
            produced = {"pb": "pages_mono", "cor": "pages_color", "total": "pages_total"}.get(key)
            if pc is not None and produced is not None and not (key == "cor" and not color):
                row[f"diff_{key}"] = getattr(pc, produced)
            else:
                row[f"diff_{key}"] = None if a is None or b is None else max(b - a, 0)
        rows.append(row)
    data.export_groups = [(label, len(cols)) for label, cols in groups]
    data.export_columns = [c for _label, cols in groups for c in cols]
    data.export_rows = rows
    data.export_totals = {f"diff_{k}": sum(r[f"diff_{k}"] or 0 for r in rows) for k, _lb, _f in _SNAP_FIELDS}


DAILY_COLS = [
    Col("day", "Dia", "date"),
    Col("read_at", "Hora"),
    Col("counter_mono", "Contador PB", "int"),
    Col("counter_color", "Contador cor", "int"),
    Col("counter_total", "Contador total", "int"),
    Col("pages_mono", "Páginas PB", "int"),
    Col("pages_color", "Páginas cor", "int"),
    Col("pages_total", "Páginas no dia", "int"),
]
PERIOD_COLS = [
    Col("info", "Contador"),
    Col("first", "Leitura inicial", "int"),
    Col("last", "Leitura final", "int"),
    Col("diff", "Diferença (páginas)", "int"),
]


def _daily_rows(
    base: dict[str, Any], days: dict[Any, counters.DeviceDay], f: Filters, *, color: bool, only_read: bool
) -> list[dict[str, Any]]:
    out = []
    day = need(f.date_from, "date_from") - timedelta(days=1)
    while day < need(f.date_to, "date_to"):
        day += timedelta(days=1)
        dd = days.get(day)
        if dd is None and only_read:
            continue
        out.append(
            {
                **base,
                "day": day,
                "read_at": dd.read_at.astimezone(DISPLAY_TZ).strftime("%H:%M") if dd else None,
                "counter_mono": dd.mono if dd else None,
                "counter_color": (dd.color if dd else None) if color else None,
                "counter_total": dd.total if dd else None,
                "pages_mono": dd.pages_mono if dd else 0,
                "pages_color": (dd.pages_color if dd else 0) if color else None,
                "pages_total": dd.pages_total if dd else 0,
            }
        )
    return out


def _per_day(rows: list[dict[str, Any]], f: Filters) -> list[dict[str, Any]]:
    """Páginas de todos os equipamentos por dia (o gráfico mostra todos os dias, com ou sem leitura)."""
    per_day: dict[Any, dict[str, Any]] = {}
    day = need(f.date_from, "date_from")
    while day <= need(f.date_to, "date_to"):
        per_day[day] = {"day": day, "mono": 0, "color": 0}
        day += timedelta(days=1)
    for r in rows:
        g = per_day[r["day"]]
        g["mono"] += r["pages_mono"] or 0
        g["color"] += r["pages_color"] or 0
    return list(per_day.values())


def _snap(s: counters.Snapshot | None, field: str) -> int | None:
    return None if s is None else getattr(s, field)


def _a4(s: counters.Snapshot | None, full: str, big: str) -> int | None:
    """A4 = contador da cor - A3 (os A3 fazem parte do total da cor)."""
    a = _snap(s, full)
    return None if a is None else a - (_snap(s, big) or 0)


def _period_lines(
    pc: counters.PeriodCounters | None, pages: dict[str, int | None], *, color: bool
) -> list[dict[str, Any]]:
    """Linhas do bloco no resumo do período: PB (A4/A3), cor (A4/A3), total geral e digitalizações."""
    start, end = (pc.start, pc.end) if pc else (None, None)
    snaps = (start, end)
    large = any(_snap(s, "mono_large") is not None or _snap(s, "color_large") is not None for s in snaps)
    groups = [("PB", "mono", "mono_large", pages["pages_mono"])]
    if color:
        groups.append(("Cor", "color", "color_large", pages["pages_color"]))
    out = []

    def line(label: str, first: int | None, last: int | None, diff: int | None) -> None:
        if first is None and last is None and label != "Total geral":
            return  # o equipamento não informa esse contador (ex.: só o total)
        out.append({"info": label, "first": first, "last": last, "diff": diff})

    def diff_of(a: int | None, b: int | None) -> int | None:
        return None if a is None or b is None else max(b - a, 0)

    for name, full, big, produced in groups:
        if large:
            line(
                f"{name} A4",
                _a4(start, full, big),
                _a4(end, full, big),
                diff_of(_a4(start, full, big), _a4(end, full, big)),
            )
            line(f"{name} A3", _snap(start, big), _snap(end, big), pc.diff(big) if pc else None)
        line(f"Total {name}", _snap(start, full), _snap(end, full), produced)
    line("Total geral", _snap(start, "total"), _snap(end, "total"), pages["pages_total"])
    if any(_snap(s, "scan") is not None for s in snaps):
        line("Digitalizações", _snap(start, "scan"), _snap(end, "scan"), pc.diff("scan") if pc else None)
    return out


async def cutoff(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    """Leitura de corte: a leitura válida de cada equipamento mais próxima e anterior ao fim da data."""
    await check_customer(session, p, f)
    scope, params = sql_scope(p, f)
    got = await counters.cutoff(session, scope, params, f.hi)
    rows = []
    for d, cname, erp, sname, _agent in await _devices(session, p, f):
        c = got.get(d.id)
        rows.append(
            {
                **_dev(d, cname, sname),
                "erp_code": erp,
                "read_at": c.read_at if c else None,
                "total": c.total if c else None,
                "mono": c.mono if c else None,
                "color": c.color if c else None,
            }
        )
    cols = [
        C_CUSTOMER,
        Col("erp_code", "Código ERP"),
        C_SITE,
        C_SERIAL,
        C_PAT,
        C_MODEL,
        Col("read_at", "Data da leitura", "datetime"),
        Col("total", "Total", "int"),
        Col("mono", "PB", "int"),
        Col("color", "Cor", "int"),
    ]
    missing = sum(1 for r in rows if r["read_at"] is None)
    notes = [f"{missing} equipamento(s) sem leitura válida até a data de corte."] if missing else []
    return ReportData(cols, rows, _sum(rows, ["total", "mono", "color"]), notes=notes)


def _money(v: Decimal | None) -> Decimal | None:
    return None if v is None else v.quantize(Decimal("0.01"))


async def billing(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    """Cobrança do mês: leituras de corte no início e no fim, páginas do mês, franquia e excedente."""
    await check_customer(session, p, f)
    lo, hi = parse_month(need(f.month, "month"))
    scope, params = sql_scope(p, f)
    start = await counters.cutoff(session, scope, params, lo)
    end = await counters.cutoff(session, scope, params, hi)
    prod = await counters.production_by_device(session, scope, params, lo, hi)
    rows = []
    for d, cname, erp, sname, _agent in await _devices(session, p, f):
        s, e, pr = start.get(d.id), end.get(d.id), prod.get(d.id)
        pages_mono = pr.mono if pr else 0
        pages_color = pr.color if pr else 0
        fr_mono, fr_color = d.franchise_pages_mono or 0, d.franchise_pages_color or 0
        ex_mono, ex_color = max(pages_mono - fr_mono, 0), max(pages_color - fr_color, 0)
        val_mono = _money(d.overage_price_mono * ex_mono) if d.overage_price_mono is not None else None
        val_color = _money(d.overage_price_color * ex_color) if d.overage_price_color is not None else None
        overage = (val_mono or Decimal(0)) + (val_color or Decimal(0))
        franchise = d.franchise_value
        billed = (
            None
            if franchise is None and val_mono is None and val_color is None
            else ((franchise or Decimal(0)) + overage)
        )
        rows.append(
            {
                **_dev(d, cname, sname),
                "erp_code": erp,
                "start_mono": s.mono if s else None,
                "start_color": s.color if s else None,
                "end_mono": e.mono if e else None,
                "end_color": e.color if e else None,
                "pages_mono": pages_mono,
                "pages_color": pages_color,
                "franchise_mono": d.franchise_pages_mono,
                "franchise_color": d.franchise_pages_color,
                "excess_mono": ex_mono,
                "excess_color": ex_color,
                "franchise_value": _money(franchise),
                "overage_value": _money(overage) if (val_mono is not None or val_color is not None) else None,
                "billed": _money(billed),
            }
        )
    cols = [
        C_CUSTOMER,
        Col("erp_code", "Código ERP"),
        C_SERIAL,
        C_PAT,
        C_MODEL,
        Col("start_mono", "PB inicial", "int"),
        Col("end_mono", "PB final", "int"),
        Col("start_color", "Cor inicial", "int"),
        Col("end_color", "Cor final", "int"),
        Col("pages_mono", "Páginas PB", "int"),
        Col("pages_color", "Páginas cor", "int"),
        Col("franchise_mono", "Franquia PB", "int"),
        Col("franchise_color", "Franquia cor", "int"),
        Col("excess_mono", "Excedente PB", "int"),
        Col("excess_color", "Excedente cor", "int"),
        Col("franchise_value", "Valor da franquia", "money"),
        Col("overage_value", "Valor do excedente", "money"),
        Col("billed", "Total a cobrar", "money"),
    ]
    totals = _sum(
        rows,
        [
            "pages_mono",
            "pages_color",
            "excess_mono",
            "excess_color",
            "franchise_value",
            "overage_value",
            "billed",
        ],
    )
    notes = [
        "Páginas do mês = soma dos aumentos entre leituras válidas (regressões e trocas de placa não "
        "geram páginas negativas nem duplicadas).",
    ]
    return ReportData(cols, rows, totals, notes=notes)


# ----------------------------------------------------------------------------- suprimentos


async def _replacements(session: AsyncSession, p: Principal, f: Filters) -> Any:
    await check_customer(session, p, f)
    return (
        (
            await session.execute(
                select(SupplyReplacement, Device, Customer.name, Site.name)
                .join(Device, Device.id == SupplyReplacement.device_id)
                .join(Customer, Customer.id == Device.customer_id)
                .join(Site, Site.id == Device.site_id)
                .where(
                    device_scope(p, f),
                    SupplyReplacement.replaced_at >= f.lo,
                    SupplyReplacement.replaced_at < f.hi,
                )
                .order_by(SupplyReplacement.replaced_at.desc())
            )
        )
        .tuples()
        .all()
    )


def _capacity_pct(yield_pages: int | None, nominal: int | None) -> Decimal | None:
    if not yield_pages or not nominal:
        return None
    return (Decimal(yield_pages) * 100 / Decimal(nominal)).quantize(Decimal("0.1"))


async def supply_replacements(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    rows = [
        {
            "replaced_at": r.replaced_at,
            **_dev(d, cname, sname),
            "supply": r.description or r.supply_key,
            "color": COLOR_LABELS.get(r.color or "", r.color),
            "level_before": r.level_before,
            "level_after": r.level_after,
            "yield_pages": r.yield_pages,
            "nominal_capacity": r.nominal_capacity,
            "capacity_pct": _capacity_pct(r.yield_pages, r.nominal_capacity),
            "premature": r.premature,
        }
        for r, d, cname, sname in await _replacements(session, p, f)
    ]
    cols = [
        Col("replaced_at", "Troca em", "datetime"),
        C_CUSTOMER,
        C_SERIAL,
        C_MODEL,
        Col("supply", "Suprimento"),
        Col("color", "Cor"),
        Col("level_before", "Nível antes", "percent"),
        Col("level_after", "Nível depois", "percent"),
        Col("yield_pages", "Rendimento (páginas)", "int"),
        Col("nominal_capacity", "Capacidade nominal", "int"),
        Col("capacity_pct", "% da capacidade", "percent"),
        Col("premature", "Prematura", "bool"),
    ]
    return ReportData(cols, rows, _sum(rows, ["yield_pages"]))


async def supply_yield(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    """Consumo e rendimento de toner por modelo e cartucho (16.3)."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r, d, _c, _s in await _replacements(session, p, f):
        model = " ".join(x for x in (d.brand, d.model) if x) or "(sem modelo)"
        cart = r.description or r.supply_key
        g = groups.setdefault(
            (model, cart, r.color or ""),
            {"yields": [], "nominal": set(), "count": 0, "premature": 0, "devices": set()},
        )
        g["count"] += 1
        g["premature"] += int(r.premature)
        g["devices"].add(d.id)
        if r.yield_pages:
            g["yields"].append(r.yield_pages)
        if r.nominal_capacity:
            g["nominal"].add(r.nominal_capacity)
    rows = []
    for (model, cart, color), g in sorted(groups.items()):
        ys: list[int] = g["yields"]
        nominal = max(g["nominal"]) if g["nominal"] else None
        avg = round(sum(ys) / len(ys)) if ys else None
        rows.append(
            {
                "model": model,
                "cartridge": cart,
                "color": COLOR_LABELS.get(color, color or None),
                "devices": len(g["devices"]),
                "count": g["count"],
                "premature": g["premature"],
                "yield_avg": avg,
                "yield_min": min(ys) if ys else None,
                "yield_max": max(ys) if ys else None,
                "nominal": nominal,
                "capacity_pct": _capacity_pct(avg, nominal),
            }
        )
    cols = [
        C_MODEL,
        Col("cartridge", "Cartucho"),
        Col("color", "Cor"),
        Col("devices", "Equipamentos", "int"),
        Col("count", "Trocas", "int"),
        Col("premature", "Prematuras", "int"),
        Col("yield_avg", "Rendimento médio", "int"),
        Col("yield_min", "Mínimo", "int"),
        Col("yield_max", "Máximo", "int"),
        Col("nominal", "Capacidade nominal", "int"),
        Col("capacity_pct", "% médio da capacidade", "percent"),
    ]
    notes = ["Prematura = trocado com mais de 20% de nível. O rendimento considera só trocas com contador."]
    return ReportData(cols, rows, _sum(rows, ["count", "premature"]), notes=notes)


async def supply_alerts(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    await check_customer(session, p, f)
    when = Alert.opened_at if f.date_type != "resolved" else Alert.resolved_at
    stmt = (
        select(Alert, Device, Customer.name)
        .join(Device, and_(Alert.target_type == "device", Device.id == Alert.target_id))
        .join(Customer, Customer.id == Device.customer_id)
        .where(
            device_scope(p, f),
            Alert.type.in_(("toner_low", "toner_days_left")),
            when >= f.lo,
            when < f.hi,
        )
        .order_by(when.desc())
    )
    rows = [
        {
            "opened_at": a.opened_at,
            "customer": cname,
            "serial": d.serial,
            "model": d.model,
            "message": a.message,
            "severity": SEVERITY_LABELS.get(a.severity, a.severity),
            "state": ALERT_STATE_LABELS.get(a.state, a.state),
            "resolved_at": a.resolved_at,
        }
        for a, d, cname in (await session.execute(stmt)).tuples()
    ]
    cols = [
        Col("opened_at", "Aberto em", "datetime"),
        C_CUSTOMER,
        C_SERIAL,
        C_MODEL,
        Col("message", "Alerta"),
        Col("severity", "Severidade"),
        Col("state", "Estado"),
        Col("resolved_at", "Resolvido em", "datetime"),
    ]
    return ReportData(cols, rows)


# ----------------------------------------------------------------------------- parque


async def _levels(session: AsyncSession, device_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not device_ids:
        return {}
    out: dict[uuid.UUID, list[str]] = defaultdict(list)
    rows = await session.execute(
        select(SupplyCurrent.device_id, SupplyCurrent.color, SupplyCurrent.percent)
        .where(
            SupplyCurrent.device_id.in_(device_ids),
            SupplyCurrent.supply_class == "consumed",
            SupplyCurrent.color.is_not(None),
        )
        .order_by(SupplyCurrent.device_id, SupplyCurrent.color)
    )
    short = {"black": "K", "cyan": "C", "magenta": "M", "yellow": "Y"}
    for device_id, color, pct in rows.tuples():
        label = short.get(color or "", color or "?")
        out[device_id].append(f"{label} {'n/d' if pct is None else f'{pct:.0f}%'}")
    return {k: " · ".join(v) for k, v in out.items()}


def _park_row(
    d: Device, cname: str, sname: str, agent: str | None, levels: dict[uuid.UUID, str]
) -> dict[str, Any]:
    return {
        **_dev(d, cname, sname, agent),
        "status": DEVICE_STATUS_LABELS.get(d.last_status, d.last_status),
        "last_read_at": d.last_read_at,
        "first_seen_at": d.first_seen_at,
        "total": d.last_total,
        "mono": d.last_mono,
        "color": d.last_color,
        "levels": levels.get(d.id),
    }


PARK_COLS = [
    C_CUSTOMER,
    C_SITE,
    Col("status", "Status"),
    C_IP,
    C_SERIAL,
    C_PAT,
    C_BRAND,
    C_MODEL,
    C_SECTOR,
    Col("last_read_at", "Comunicação", "datetime"),
    Col("total", "Total", "int"),
    Col("mono", "PB", "int"),
    Col("color", "Cor", "int"),
    Col("levels", "Níveis"),
    C_AGENT,
]


async def _park(session: AsyncSession, p: Principal, f: Filters, *where: ColumnElement[bool]) -> ReportData:
    devices = await _devices(session, p, f, *where)
    levels = await _levels(session, [d.id for d, *_ in devices])
    rows = [_park_row(d, cname, sname, agent, levels) for d, cname, _erp, sname, agent in devices]
    return ReportData(PARK_COLS, rows)


def _is_offline() -> ColumnElement[bool]:
    return or_(Device.disconnected.is_(True), Device.last_status == "offline")


async def park_overview(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    return await _park(session, p, f, Device.active.is_(True))


async def online(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    return await _park(session, p, f, Device.active.is_(True), ~_is_offline())


async def disconnected(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    data = await _park(session, p, f, Device.active.is_(True), _is_offline())
    now = datetime.now(UTC)
    for r in data.rows:
        last = r["last_read_at"]
        r["hours_without"] = None if last is None else int((now - last).total_seconds() // 3600)
    data.columns = [*PARK_COLS[:10], Col("hours_without", "Horas sem comunicação", "int"), *PARK_COLS[10:]]
    return data


async def deactivated(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    devices = await _devices(session, p, f, Device.active.is_(False))
    ids = [d.id for d, *_ in devices]
    when = (
        dict(
            (
                await session.execute(
                    select(DeviceEvent.device_id, func.max(DeviceEvent.created_at))
                    .where(DeviceEvent.device_id.in_(ids), DeviceEvent.type == "deactivated")
                    .group_by(DeviceEvent.device_id)
                )
            )
            .tuples()
            .all()
        )
        if ids
        else {}
    )
    levels = await _levels(session, ids)
    rows = [
        {**_park_row(d, cname, sname, agent, levels), "deactivated_at": when.get(d.id)}
        for d, cname, _erp, sname, agent in devices
    ]
    cols = [*PARK_COLS[:2], Col("deactivated_at", "Desativado em", "datetime"), *PARK_COLS[3:]]
    return ReportData(cols, rows)


async def park_status(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    await check_customer(session, p, f)
    active = Device.active.is_(True)
    offline = and_(active, _is_offline())
    stmt = (
        select(
            Customer.name,
            func.count().filter(Device.discovery_state == "approved"),
            func.count().filter(and_(active, Device.discovery_state == "approved", ~_is_offline())),
            func.count().filter(and_(offline, Device.discovery_state == "approved")),
            func.count().filter(and_(Device.active.is_(False), Device.discovery_state == "approved")),
            func.count().filter(
                and_(active, Device.last_status == "error", Device.discovery_state == "approved")
            ),
            func.count().filter(
                and_(active, Device.last_status == "warning", Device.discovery_state == "approved")
            ),
            func.count().filter(
                and_(active, Device.last_status == "energy_saving", Device.discovery_state == "approved")
            ),
            func.count().filter(Device.discovery_state == "pending"),
        )
        .join(Customer, Customer.id == Device.customer_id)
        .where(device_scope(p, f, approved=False))
        .group_by(Customer.name)
        .order_by(Customer.name)
    )
    keys = ["devices", "online", "offline", "inactive", "error", "warning", "energy_saving", "pending"]
    rows = [
        {"customer": r[0], **dict(zip(keys, (int(x) for x in r[1:]), strict=True))}
        for r in (await session.execute(stmt)).all()
    ]
    cols = [
        C_CUSTOMER,
        Col("devices", "Equipamentos", "int"),
        Col("online", "Online", "int"),
        Col("offline", "Sem conexão", "int"),
        Col("inactive", "Desativados", "int"),
        Col("error", "Em erro", "int"),
        Col("warning", "Em atenção", "int"),
        Col("energy_saving", "Economia de energia", "int"),
        Col("pending", "Pendentes em Descobertas", "int"),
    ]
    return ReportData(cols, rows, _sum(rows, keys))


async def discoveries(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    await check_customer(session, p, f)
    when = Device.discovery_decided_at if f.date_type == "decided" else Device.first_seen_at
    stmt = _device_select(p, f, approved=False).where(when >= f.lo, when < f.hi).order_by(when.desc())
    rows = [
        {
            "first_seen_at": d.first_seen_at,
            "state": DISCOVERY_LABELS.get(d.discovery_state, d.discovery_state),
            "decided_at": d.discovery_decided_at,
            **_dev(d, cname, sname, agent),
        }
        for d, cname, _erp, sname, agent in (await session.execute(stmt)).tuples()
    ]
    cols = [
        Col("first_seen_at", "Descoberto em", "datetime"),
        Col("state", "Situação"),
        Col("decided_at", "Decidido em", "datetime"),
        C_CUSTOMER,
        C_SITE,
        C_IP,
        C_SERIAL,
        C_BRAND,
        C_MODEL,
        C_AGENT,
    ]
    return ReportData(cols, rows)


async def no_reading(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    limit = datetime.now(UTC) - timedelta(hours=need(f.hours, "hours"))
    data = await _park(
        session,
        p,
        f,
        Device.active.is_(True),
        or_(Device.last_read_at.is_(None), Device.last_read_at < limit),
    )
    now = datetime.now(UTC)
    for r in data.rows:
        last = r["last_read_at"]
        r["hours_without"] = None if last is None else int((now - last).total_seconds() // 3600)
    data.columns = [
        C_CUSTOMER,
        C_SITE,
        C_IP,
        C_SERIAL,
        C_MODEL,
        Col("last_read_at", "Última leitura", "datetime"),
        Col("hours_without", "Horas sem leitura", "int"),
        Col("status", "Status"),
        C_AGENT,
    ]
    return data


async def _events(
    session: AsyncSession,
    p: Principal,
    f: Filters,
    types: tuple[str, ...],
    when: Any = None,
) -> Any:
    when = DeviceEvent.created_at if when is None else when
    await check_customer(session, p, f)
    return (
        (
            await session.execute(
                select(DeviceEvent, Device, Customer.name)
                .join(Device, Device.id == DeviceEvent.device_id)
                .join(Customer, Customer.id == Device.customer_id)
                .where(
                    device_scope(p, f),
                    DeviceEvent.type.in_(types),
                    when >= f.lo,
                    when < f.hi,
                )
                .order_by(when.desc())
            )
        )
        .tuples()
        .all()
    )


async def ip_changes(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    sites = {
        str(sid): name
        for sid, name in (
            await session.execute(select(Site.id, Site.name).where(reseller_scope(p, Site.reseller_id)))
        ).tuples()
    }
    rows = []
    for ev, d, cname in await _events(session, p, f, ("ip_changed", "replaced", "moved_site")):
        data = ev.data
        if ev.type == "ip_changed":
            before, after = (
                f"{data.get('old_ip')}:{data.get('old_port')}",
                f"{data.get('new_ip')}:{data.get('new_port')}",
            )
        elif ev.type == "replaced":
            before, after = f"{d.serial} em {data.get('ip')}", f"{data.get('new_serial')}"
        else:
            before = sites.get(str(data.get("from_site_id")), "—")
            after = sites.get(str(data.get("to_site_id")), "—")
        rows.append(
            {
                "at": ev.created_at,
                "type": EVENT_LABELS.get(ev.type, ev.type),
                "customer": cname,
                "serial": d.serial,
                "model": d.model,
                "before": before,
                "after": after,
            }
        )
    cols = [
        Col("at", "Quando", "datetime"),
        Col("type", "Tipo"),
        C_CUSTOMER,
        C_SERIAL,
        C_MODEL,
        Col("before", "Antes"),
        Col("after", "Depois"),
    ]
    return ReportData(cols, rows)


async def regressions(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    # Tipo de data: a da leitura que regrediu (padrão) ou a do recebimento pelo servidor.
    read_at = cast(DeviceEvent.data.op("->>")("read_at"), DateTime(timezone=True))
    when = DeviceEvent.created_at if f.date_type == "received" else read_at
    events = await _events(session, p, f, ("counter_regression",), when)
    reading_ids = [uuid.UUID(ev.data["reading_id"]) for ev, _d, _c in events if ev.data.get("reading_id")]
    reviews = (
        dict(
            (
                await session.execute(
                    select(ReadingReview.reading_id, ReadingReview.classification).where(
                        ReadingReview.reading_id.in_(reading_ids)
                    )
                )
            )
            .tuples()
            .all()
        )
        if reading_ids
        else {}
    )
    names = {"total": "Total", "mono": "PB", "color": "Cor"}
    rows = []
    for ev, d, cname in events:
        rid = ev.data.get("reading_id")
        review = reviews.get(uuid.UUID(rid)) if rid else None
        for counter, label in names.items():
            change = ev.data.get(counter)
            if not isinstance(change, dict):
                continue
            rows.append(
                {
                    "at": datetime.fromisoformat(ev.data["read_at"])
                    if ev.data.get("read_at")
                    else ev.created_at,
                    "received_at": ev.created_at,
                    "customer": cname,
                    "serial": d.serial,
                    "model": d.model,
                    "counter": label,
                    "before": change.get("before"),
                    "after": change.get("after"),
                    "review": REVIEW_LABELS.get(review or "", "Não classificada"),
                }
            )
    cols = [
        Col("at", "Leitura", "datetime"),
        Col("received_at", "Recebida", "datetime"),
        C_CUSTOMER,
        C_SERIAL,
        C_MODEL,
        Col("counter", "Contador"),
        Col("before", "Antes", "int"),
        Col("after", "Depois", "int"),
        Col("review", "Classificação"),
    ]
    notes = ["Leituras com regressão ficam fora da produção até serem classificadas como válidas."]
    return ReportData(cols, rows, notes=notes)


# ----------------------------------------------------------------------------- coletores


def _agent_select(p: Principal, f: Filters) -> Any:
    return (
        select(Agent, Site.name, Customer.name)
        .join(Site, Site.id == Agent.site_id)
        .join(Customer, Customer.id == Site.customer_id)
        .where(
            Agent.deleted_at.is_(None),
            Agent.revoked_at.is_(None),
            Agent.enrolled_at.is_not(None),
            reseller_scope(p, Agent.reseller_id),
            customer_scope(p, Site.customer_id),
            Site.customer_id == f.customer_id if f.customer_id else true(),
            Site.id == f.site_id if f.site_id else true(),
        )
        .order_by(Customer.name, Site.name, Agent.name)
    )


async def agent_status(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    await check_customer(session, p, f)
    agents = (await session.execute(_agent_select(p, f))).tuples().all()
    counts = (
        dict(
            (
                await session.execute(
                    select(Device.last_agent_id, func.count())
                    .where(
                        Device.last_agent_id.in_([a.id for a, *_ in agents]),
                        Device.deleted_at.is_(None),
                        Device.active.is_(True),
                    )
                    .group_by(Device.last_agent_id)
                )
            )
            .tuples()
            .all()
        )
        if agents
        else {}
    )
    rows = [
        {
            "customer": cname,
            "site": sname,
            "agent": a.name,
            "state": AGENT_STATE_LABELS.get(a.state, a.state),
            "role": "MASTER" if a.cluster_role == "master" else "STANDBY",
            "hostname": a.hostname,
            "public_ip": a.public_ip,
            "version": a.version,
            "last_seen_at": a.last_seen_at,
            "queue": a.queue_pending,
            "devices": counts.get(a.id, 0),
        }
        for a, sname, cname in agents
    ]
    cols = [
        C_CUSTOMER,
        C_SITE,
        Col("agent", "Coletor"),
        Col("state", "Estado"),
        Col("role", "Papel"),
        Col("hostname", "Hostname"),
        Col("public_ip", "IP público"),
        Col("version", "Versão"),
        Col("last_seen_at", "Último sinal", "datetime"),
        Col("queue", "Fila", "int"),
        Col("devices", "Equipamentos", "int"),
    ]
    return ReportData(cols, rows)


async def agent_availability(session: AsyncSession, p: Principal, f: Filters) -> ReportData:
    """% do tempo online: janelas de 5 min com ao menos um heartbeat, desde o cadastro do coletor."""
    await check_customer(session, p, f)
    now = datetime.now(UTC)
    retention = now - timedelta(days=HEARTBEAT_RETENTION_DAYS)
    lo, hi = max(f.lo, retention), min(f.hi, now)
    agents = (await session.execute(_agent_select(p, f))).tuples().all()
    buckets: dict[uuid.UUID, int] = {}
    if agents and hi > lo:
        sql = text(
            """
            SELECT agent_id, count(DISTINCT floor(extract(epoch FROM ts) / :bucket))
            FROM agent_heartbeats
            WHERE agent_id = ANY(:ids) AND ts >= :lo AND ts < :hi
            GROUP BY agent_id
            """
        )
        buckets = {
            r[0]: int(r[1])
            for r in await session.execute(
                sql,
                {
                    "ids": [a.id for a, *_ in agents],
                    "lo": lo,
                    "hi": hi,
                    "bucket": AVAILABILITY_BUCKET.total_seconds(),
                },
            )
        }
    per_agent = []
    for a, sname, cname in agents:
        start = max(lo, a.enrolled_at or lo)
        expected = max(int((hi - start) / AVAILABILITY_BUCKET), 0)
        seen = min(buckets.get(a.id, 0), expected)
        pct = (Decimal(seen) * 100 / Decimal(expected)).quantize(Decimal("0.1")) if expected else None
        per_agent.append(
            {
                "customer": cname,
                "site": sname,
                "agent": a.name,
                "online_pct": pct,
                "hours_online": round(seen * AVAILABILITY_BUCKET.total_seconds() / 3600, 1),
                "hours_period": round(expected * AVAILABILITY_BUCKET.total_seconds() / 3600, 1),
            }
        )
    notes = []
    if f.lo < retention:
        notes.append(f"Heartbeats ficam {HEARTBEAT_RETENTION_DAYS} dias: o cálculo começa em {lo:%d/%m/%Y}.")
    if f.group_by == "customer":
        groups: dict[str, dict[str, Any]] = {}
        for r in per_agent:
            g = groups.setdefault(
                r["customer"], {"customer": r["customer"], "agents": 0, "on": 0.0, "all": 0.0}
            )
            g["agents"] += 1
            g["on"] += r["hours_online"]
            g["all"] += r["hours_period"]
        rows = [
            {
                "customer": g["customer"],
                "agents": g["agents"],
                "online_pct": (Decimal(g["on"] * 100 / g["all"])).quantize(Decimal("0.1"))
                if g["all"]
                else None,
            }
            for g in groups.values()
        ]
        cols = [
            C_CUSTOMER,
            Col("agents", "Coletores", "int"),
            Col("online_pct", "% do tempo online", "percent"),
        ]
        return ReportData(cols, rows, notes=notes)
    cols = [
        C_CUSTOMER,
        C_SITE,
        Col("agent", "Coletor"),
        Col("online_pct", "% do tempo online", "percent"),
        Col("hours_online", "Horas online", "decimal"),
        Col("hours_period", "Horas no período", "decimal"),
    ]
    return ReportData(cols, per_agent, notes=notes)


# ----------------------------------------------------------------------------- registro

PRODUCTION_GROUPS = (
    Option("device", "Por equipamento"),
    Option("site", "Por local"),
    Option("customer", "Por cliente"),
)
PRODUCTION = "Produção e cobrança"
SUPPLIES = "Suprimentos"
PARK = "Parque"
AGENTS = "Coletores"

for _defn in (
    ReportDef(
        "production",
        "Produção por período",
        PRODUCTION,
        "Páginas PB e cor por equipamento, local ou cliente.",
        production,
        group_by=PRODUCTION_GROUPS,
    ),
    ReportDef(
        "device_counters",
        "Contadores por equipamento",
        PRODUCTION,
        "Leitura inicial, final e páginas de cada equipamento, ou o contador de cada dia.",
        device_counters,
        group_by=DEVICE_COUNTER_VIEWS,
        group_label="Exibição",
    ),
    ReportDef(
        "daily_counter",
        "Contador diário",
        PRODUCTION,
        "Páginas impressas por dia em cada equipamento (uma coluna por dia) e o total.",
        daily_counter,
        max_days=DAILY_MAX_DAYS,
    ),
    ReportDef(
        "cutoff",
        "Leitura de corte",
        PRODUCTION,
        "Leitura de cada equipamento mais próxima e anterior à data de corte.",
        cutoff,
        period=False,
        cutoff_date=True,
    ),
    ReportDef(
        "billing",
        "Cobrança do mês",
        PRODUCTION,
        "Leituras inicial e final, franquia e excedente.",
        billing,
        period=False,
        month=True,
    ),
    ReportDef(
        "supply_replacements",
        "Trocas de suprimento",
        SUPPLIES,
        "Cada troca com rendimento do cartucho anterior.",
        supply_replacements,
    ),
    ReportDef(
        "supply_yield",
        "Consumo e rendimento de toner",
        SUPPLIES,
        "Trocas e rendimento médio por modelo e cartucho.",
        supply_yield,
        default_days=180,
    ),
    ReportDef(
        "supply_alerts",
        "Alertas de suprimento",
        SUPPLIES,
        "Toner baixo e previsão de término.",
        supply_alerts,
        date_types=(Option("opened", "Abertura"), Option("resolved", "Resolução")),
    ),
    ReportDef(
        "park_overview",
        "Visão do parque",
        PARK,
        "Equipamentos ativos com contadores e níveis.",
        park_overview,
        period=False,
    ),
    ReportDef(
        "park_status",
        "Status do parque",
        PARK,
        "Contagem por situação, por cliente.",
        park_status,
        period=False,
    ),
    ReportDef("online", "Online", PARK, "Equipamentos comunicando.", online, period=False),
    ReportDef(
        "disconnected",
        "Sem conexão",
        PARK,
        "Equipamentos ativos que não respondem.",
        disconnected,
        period=False,
    ),
    ReportDef("deactivated", "Desativados", PARK, "Equipamentos desativados.", deactivated, period=False),
    ReportDef(
        "no_reading",
        "Equipamentos sem leitura",
        PARK,
        "Sem leitura há mais de N horas.",
        no_reading,
        period=False,
        hours=True,
    ),
    ReportDef(
        "discoveries",
        "Descobertas",
        PARK,
        "Equipamentos descobertos e decisões.",
        discoveries,
        date_types=(Option("first_seen", "Descoberta"), Option("decided", "Decisão")),
    ),
    ReportDef(
        "ip_changes",
        "Trocas de IP e de equipamento",
        PARK,
        "Mudanças de IP, substituições e mudanças de local.",
        ip_changes,
    ),
    ReportDef(
        "regressions",
        "Regressões de contador",
        PARK,
        "Contadores que voltaram e a classificação.",
        regressions,
    ),
    ReportDef(
        "agent_status",
        "Status dos coletores",
        AGENTS,
        "Estado, versão, fila e equipamentos.",
        agent_status,
        period=False,
    ),
    ReportDef(
        "agent_availability",
        "Disponibilidade dos coletores",
        AGENTS,
        "% do tempo online.",
        agent_availability,
        default_days=30,
        group_by=(Option("agent", "Por coletor"), Option("customer", "Por cliente")),
    ),
):
    register(_defn)
