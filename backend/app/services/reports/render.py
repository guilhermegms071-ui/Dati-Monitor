"""Report output: JSON cells for the screen, and CSV / XLSX / PDF files with the same columns."""

import io
from datetime import date, datetime
from decimal import Decimal
from importlib.resources import files
from typing import Any, Literal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.errors import bad_request
from app.services.export import DISPLAY_TZ, ExportColumn, to_csv, to_xlsx
from app.services.reports.base import SECTION_KEY, Col, ReportData, Section, Stat

FileFormat = Literal["csv", "xlsx", "pdf"]
MAX_PDF_ROWS = 5000
MEDIA = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def json_cell(value: Any) -> Any:
    """Decimals as numbers (the portal formats them), datetimes/dates as ISO."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def fmt_int(v: int | float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def fmt_dec(v: Decimal | float, places: int = 1) -> str:
    s = f"{v:,.{places}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def text_cell(col: Col, value: Any) -> str:
    """How a value is printed in the PDF (pt-BR)."""
    if value is None or value == "":
        return "—"
    match col.kind:
        case "int":
            return fmt_int(value)
        case "money":
            return "R$ " + fmt_dec(value, 2)
        case "percent":
            return fmt_dec(value) + "%"
        case "decimal":
            return fmt_dec(value)
        case "bool":
            return "Sim" if value else "Não"
        case "datetime":
            return (
                value.astimezone(DISPLAY_TZ).strftime("%d/%m/%Y %H:%M")
                if isinstance(value, datetime)
                else str(value)
            )
        case "date":
            return value.strftime("%d/%m/%Y") if isinstance(value, date) else str(value)
    return str(value)


def _export_cols(data: ReportData) -> list[ExportColumn[dict[str, Any]]]:
    def getter(key: str) -> Any:
        return lambda row: row.get(key)

    return [ExportColumn(c.label, getter(c.key)) for c in data.columns]


def _rows_with_totals(data: ReportData) -> list[dict[str, Any]]:
    if not data.totals:
        return data.rows
    first = data.columns[0].key
    return [*data.rows, {**data.totals, first: "Total"}]


def render(
    data: ReportData, fmt: FileFormat, *, title: str, meta: list[tuple[str, str]] | None = None
) -> bytes:
    """`meta` = filtros aplicados (rótulo, valor) mostrados no cabeçalho do PDF."""
    rows = _rows_with_totals(data)
    if fmt == "csv":
        return to_csv(rows, _export_cols(data))
    if fmt == "xlsx":
        return to_xlsx(rows, _export_cols(data), title)
    if len(rows) > MAX_PDF_ROWS:
        raise bad_request(
            "pdf_too_large",
            f"O PDF aceita até {fmt_int(MAX_PDF_ROWS)} linhas; este relatório tem {fmt_int(len(rows))}. "
            "Use CSV ou XLSX, ou filtre por cliente.",
        )
    return _pdf(data, title=title, meta=meta or [])


# Cores do portal (azul da Daticopy e cinzas neutros).
BRAND = colors.HexColor("#156cc4")
BRAND_DARK = colors.HexColor("#143e6a")
BRAND_SOFT = colors.HexColor("#eef6fe")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
LINE = colors.HexColor("#dbe2e8")
ZEBRA = colors.HexColor("#f6f8fa")
TOTAL_BG = colors.HexColor("#e6eef7")
NUMERIC = {"int", "money", "percent", "decimal"}
KEEP_TOGETHER_ROWS = 14  # bloco pequeno não quebra entre páginas


def _logo(height: float) -> Image:
    resource = files("app.assets").joinpath("logo-daticopy.png")
    if not resource.is_file():
        raise RuntimeError("logo do relatório ausente: app/assets/logo-daticopy.png")
    img = Image(io.BytesIO(resource.read_bytes()), height=height, width=height * 4 / 3)
    img.hAlign = "LEFT"
    return img


class _Styles:
    def __init__(self) -> None:
        self.title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=INK)
        self.meta = ParagraphStyle("m", fontName="Helvetica", fontSize=8.5, leading=12, textColor=INK)
        self.small = ParagraphStyle("s", fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED)
        self.cell = ParagraphStyle("c", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=INK)
        self.cell_r = ParagraphStyle("cr", parent=self.cell, alignment=2)
        self.bold = ParagraphStyle("b", parent=self.cell, fontName="Helvetica-Bold")
        self.bold_r = ParagraphStyle("br", parent=self.bold, alignment=2)
        self.head = ParagraphStyle("h", parent=self.bold, textColor=colors.white)
        self.head_r = ParagraphStyle("hr", parent=self.head, alignment=2)
        self.sec_title = ParagraphStyle(
            "st", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=BRAND_DARK
        )
        self.sec_detail = ParagraphStyle(
            "sd", fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED
        )
        self.stat_label = ParagraphStyle("sl", fontName="Helvetica", fontSize=7.5, leading=9, textColor=MUTED)
        self.stat_value = ParagraphStyle(
            "sv", fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=BRAND_DARK
        )


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def _header(title: str, meta: list[tuple[str, str]], st: _Styles, width: float) -> Table:
    generated = datetime.now(DISPLAY_TZ).strftime("%d/%m/%Y %H:%M")
    info: list[Any] = [_p(title.upper(), st.title), Spacer(1, 2 * mm)]
    info += [Paragraph(f"<b>{escape(label)}:</b> {escape(value)}", st.meta) for label, value in meta if value]
    right = Paragraph(
        f"Gerado em<br/><b>{escape(generated)}</b>", ParagraphStyle("g", parent=st.small, alignment=2)
    )
    logo_w = 30 * mm
    t = Table(
        [[_logo(22.5 * mm), info, right]], colWidths=[logo_w + 4 * mm, width - logo_w - 44 * mm, 40 * mm]
    )
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("LINEBELOW", (0, 0), (-1, 0), 1.2, BRAND),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 4 * mm),
            ]
        )
    )
    return t


def _summary(stats: list[Stat], st: _Styles, width: float) -> Table:
    cells = [
        [_p(s.label, st.stat_label), _p(text_cell(Col("v", "", s.kind), s.value), st.stat_value)]
        for s in stats
    ]
    t = Table([cells], colWidths=[width / len(stats)] * len(stats))
    style: list[Any] = [
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_SOFT),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]
    style += [("LINEAFTER", (i, 0), (i, 0), 0.6, LINE) for i in range(len(stats) - 1)]
    t.setStyle(TableStyle(style))
    return t


def _grid(
    cols: list[Col],
    rows: list[dict[str, Any]],
    st: _Styles,
    width: float,
    *,
    totals: dict[str, Any] | None = None,
    totals_label: str = "Total",
    lead: list[list[Any]] | None = None,
) -> Table:
    """Table with optional `lead` rows (section header) before the column header."""
    lead = lead or []

    def para(col: Col, value: Any, *, strong: bool = False) -> Paragraph:
        num = col.kind in NUMERIC
        style = (st.bold_r if num else st.bold) if strong else (st.cell_r if num else st.cell)
        return _p(text_cell(col, value), style)

    body: list[list[Any]] = [*lead, [_p(c.label, st.head_r if c.kind in NUMERIC else st.head) for c in cols]]
    head_at = len(lead)
    body += [[para(c, r.get(c.key)) for c in cols] for r in rows]
    if totals is not None:
        body.append(
            [
                _p(totals_label, st.bold)
                if i == 0
                else (para(c, totals.get(c.key), strong=True) if totals.get(c.key) is not None else "")
                for i, c in enumerate(cols)
            ]
        )
    t = Table(body, repeatRows=head_at + 1, colWidths=[width / len(cols)] * len(cols))
    style: list[Any] = [
        ("BACKGROUND", (0, head_at), (-1, head_at), BRAND),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, head_at + 1), (-1, -1), 0.4, LINE),
        ("BOX", (0, head_at), (-1, -1), 0.6, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for i in range(head_at + 2, len(body), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    if totals is not None:
        style.append(("BACKGROUND", (0, len(body) - 1), (-1, -1), TOTAL_BG))
    for i in range(head_at):
        style += [("SPAN", (0, i), (-1, i)), ("BACKGROUND", (0, i), (-1, i), BRAND_SOFT)]
    if head_at:
        style.append(("BOX", (0, 0), (-1, head_at - 1), 0.6, LINE))
    t.setStyle(TableStyle(style))
    return t


def _sections(data: ReportData, st: _Styles, width: float) -> list[Any]:
    sections = data.sections or {}
    cols = [c for c in data.columns if not c.section]
    out: list[Any] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in data.rows:
        grouped.setdefault(str(r.get(SECTION_KEY)), []).append(r)
    for key, rows in grouped.items():
        sec = sections.get(key) or Section(key, key)
        details = "   ·   ".join(f"<b>{escape(lb)}:</b> {escape(v)}" for lb, v in sec.details)
        pad: list[Any] = [""] * (len(cols) - 1)
        lead: list[list[Any]] = [
            [_p(sec.title, st.sec_title), *pad],
            [Paragraph(details, st.sec_detail), *pad],
        ]
        table = _grid(cols, rows, st, width, totals=sec.totals, totals_label=sec.totals_label, lead=lead)
        out += [table if len(rows) > KEEP_TOGETHER_ROWS else KeepTogether(table), Spacer(1, 5 * mm)]
    if data.totals:
        out.append(_grid(cols, [], st, width, totals=data.totals, totals_label="Total geral"))
    return out


def _pdf(data: ReportData, *, title: str, meta: list[tuple[str, str]]) -> bytes:
    buf = io.BytesIO()
    page = landscape(A4)
    margin = 12 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=page,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=10 * mm,
        bottomMargin=14 * mm,
        title=title,
        author="Daticopy · Dati Monitor",
    )
    st = _Styles()
    width = page[0] - 2 * margin
    story: list[Any] = [_header(title, meta, st, width), Spacer(1, 5 * mm)]
    if data.summary:
        story += [_summary(data.summary, st, width), Spacer(1, 4 * mm)]
    story += [_p(n, st.small) for n in data.notes]
    story.append(Spacer(1, 4 * mm))
    if not data.rows:
        story.append(_p("Nenhum registro com esses filtros.", st.meta))
    elif data.sections is not None:
        story += _sections(data, st, width)
    else:
        story.append(_grid(data.columns, data.rows, st, width, totals=data.totals))

    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(margin, 10 * mm, page[0] - margin, 10 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(margin, 6 * mm, f"Daticopy · Dati Monitor · {title}")
        canvas.drawRightString(page[0] - margin, 6 * mm, f"Página {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
