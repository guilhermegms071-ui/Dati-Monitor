"""CSV and XLSX export of any list/report (respecting the filters the caller already applied)."""

import csv
import io
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi.responses import Response
from openpyxl import Workbook

DISPLAY_TZ = ZoneInfo("America/Sao_Paulo")
ExportFormat = Literal["csv", "xlsx"]


@dataclass(frozen=True)
class ExportColumn[T]:
    header: str
    value: Callable[[T], Any]


def _cell(value: Any) -> Any:
    if isinstance(value, datetime):
        # Exibição em America/Sao_Paulo (armazenamento sempre UTC).
        return value.astimezone(DISPLAY_TZ).replace(tzinfo=None)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value)
    return value


def to_csv[T](rows: Iterable[T], columns: Sequence[ExportColumn[T]]) -> bytes:
    buf = io.StringIO()
    # ";" e BOM: abre corretamente no Excel em pt-BR.
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow([c.header for c in columns])
    for row in rows:
        values = []
        for c in columns:
            v = _cell(c.value(row))
            if isinstance(v, datetime):
                v = v.strftime("%d/%m/%Y %H:%M:%S")
            elif isinstance(v, date):
                v = v.strftime("%d/%m/%Y")
            values.append(v)
        writer.writerow(values)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def to_xlsx[T](rows: Iterable[T], columns: Sequence[ExportColumn[T]], sheet_title: str) -> bytes:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title=sheet_title[:31])
    ws.append([c.header for c in columns])
    for row in rows:
        ws.append([_cell(c.value(row)) for c in columns])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_response[T](
    rows: Iterable[T], columns: Sequence[ExportColumn[T]], *, fmt: ExportFormat, basename: str
) -> Response:
    stamp = datetime.now(DISPLAY_TZ).strftime("%Y%m%d-%H%M")
    if fmt == "csv":
        body = to_csv(rows, columns)
        media = "text/csv; charset=utf-8"
    else:
        body = to_xlsx(rows, columns, basename)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{basename}-{stamp}.{fmt}"'},
    )
