"""Report output: JSON cells for the screen, and CSV / XLSX / PDF files with the same columns."""

import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.errors import bad_request
from app.services.export import DISPLAY_TZ, ExportColumn, to_csv, to_xlsx
from app.services.reports.base import Col, ReportData

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


def render(data: ReportData, fmt: FileFormat, *, title: str, subtitle: str) -> bytes:
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
    return _pdf(data, rows, title=title, subtitle=subtitle)


def _pdf(data: ReportData, rows: list[dict[str, Any]], *, title: str, subtitle: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
        title=title,
        author="Dati Monitor",
    )
    h = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=13, leading=16)
    sub = ParagraphStyle(
        "s", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#475569")
    )
    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=6.5, leading=8)
    head = ParagraphStyle("hc", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
    numeric = {"int", "money", "percent", "decimal"}
    right = ParagraphStyle("r", parent=cell, alignment=2)

    def para(col: Col, value: Any, *, blank_empty: bool = False) -> Paragraph:
        text = "" if blank_empty and value in (None, "") else text_cell(col, value)
        return Paragraph(escape(text), right if col.kind in numeric else cell)

    table_rows: list[list[Any]] = [[Paragraph(escape(c.label), head) for c in data.columns]]
    totals_at = len(rows) - 1 if data.totals else -1
    table_rows += [
        [para(c, r.get(c.key), blank_empty=i == totals_at) for c in data.columns] for i, r in enumerate(rows)
    ]
    width = landscape(A4)[0] - 20 * mm
    table = Table(table_rows, repeatRows=1, colWidths=[width / len(data.columns)] * len(data.columns))
    style: list[Any] = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for i in range(2, len(table_rows), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f1f5f9")))
    if data.totals:
        style.append(("BACKGROUND", (0, len(table_rows) - 1), (-1, -1), colors.HexColor("#e2e8f0")))
    table.setStyle(TableStyle(style))
    generated = datetime.now(DISPLAY_TZ).strftime("%d/%m/%Y %H:%M")
    story: list[Any] = [
        Paragraph(escape(title), h),
        Paragraph(escape(f"{subtitle} · gerado em {generated} · {fmt_int(len(data.rows))} linha(s)"), sub),
    ]
    story += [Paragraph(escape(n), sub) for n in data.notes]
    story += [Spacer(1, 4 * mm), table if data.rows else Paragraph("Nenhum registro com esses filtros.", sub)]

    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawRightString(width + 10 * mm, 6 * mm, f"Dati Monitor · página {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
