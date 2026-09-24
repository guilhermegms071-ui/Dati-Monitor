"""Keyset (cursor) pagination with server-side sorting over a whitelist of sort expressions."""

import base64
import binascii
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import bad_request

MAX_PAGE_SIZE = 500
Direction = Literal["asc", "desc"]
DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True)
class SortOption:
    """A sortable expression. It must be NOT NULL (wrap with coalesce) so keyset comparisons are total."""

    expression: ColumnElement[Any] | InstrumentedAttribute[Any]
    kind: Literal["str", "int", "float", "datetime", "bool", "decimal"]


@dataclass(frozen=True)
class PageResult[T]:
    items: list[T]
    next_cursor: str | None


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _decode_value(value: Any, kind: str) -> Any:
    match kind:
        case "datetime":
            return datetime.fromisoformat(value)
        case "decimal":
            return Decimal(value)
        case "int":
            return int(value)
        case "float":
            return float(value)
        case "bool":
            return bool(value)
        case _:
            return str(value)


def encode_cursor(sort_key: str, direction: str, value: Any, row_id: uuid.UUID) -> str:
    raw = json.dumps({"s": sort_key, "d": direction, "v": _encode_value(value), "id": str(row_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, sort_key: str, direction: str, kind: str) -> tuple[Any, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if data["s"] != sort_key or data["d"] != direction:
            raise bad_request("invalid_cursor", "Cursor não corresponde à ordenação atual")
        return _decode_value(data["v"], kind), uuid.UUID(data["id"])
    except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise bad_request("invalid_cursor", "Cursor de paginação inválido") from exc


async def paginate[T](
    session: AsyncSession,
    stmt: Select[tuple[T]],
    *,
    id_column: InstrumentedAttribute[uuid.UUID],
    sort_options: Mapping[str, SortOption],
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> PageResult[T]:
    if sort not in sort_options:
        raise bad_request("invalid_sort", f"Ordenação inválida: {sort}", allowed=sorted(sort_options))
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    option = sort_options[sort]
    expr = option.expression
    if cursor:
        value, last_id = decode_cursor(cursor, sort, direction, option.kind)
        if direction == "asc":
            stmt = stmt.where(or_(expr > value, and_(expr == value, id_column > last_id)))
        else:
            stmt = stmt.where(or_(expr < value, and_(expr == value, id_column < last_id)))
    order = (expr.asc(), id_column.asc()) if direction == "asc" else (expr.desc(), id_column.desc())
    stmt = stmt.add_columns(expr.label("_sort_value")).order_by(*order).limit(limit + 1)
    rows: Sequence[Any] = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items: list[T] = [row[0] for row in rows]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(sort, direction, last._sort_value, getattr(last[0], "id"))  # noqa: B009
    return PageResult(items=items, next_cursor=next_cursor)
