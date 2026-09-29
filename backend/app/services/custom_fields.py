"""Device custom fields defined by the reseller (PROMPT 16.7): definitions CRUD and value validation."""

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, conflict, not_found
from app.core.principal import Principal
from app.models import CustomFieldDefinition
from app.schemas.custom_fields import CustomFieldIn, CustomFieldUpdate
from app.services import audit


async def list_definitions(
    session: AsyncSession, reseller_id: uuid.UUID, *, active_only: bool = False
) -> list[CustomFieldDefinition]:
    stmt = select(CustomFieldDefinition).where(CustomFieldDefinition.reseller_id == reseller_id)
    if active_only:
        stmt = stmt.where(CustomFieldDefinition.active.is_(True))
    rows = await session.execute(stmt.order_by(CustomFieldDefinition.position, CustomFieldDefinition.label))
    return list(rows.scalars())


async def list_for(session: AsyncSession, p: Principal) -> list[CustomFieldDefinition]:
    p.require("devices.read")
    return await list_definitions(session, p.reseller_id)


async def create(session: AsyncSession, p: Principal, data: CustomFieldIn) -> CustomFieldDefinition:
    p.require("settings.write")
    row = CustomFieldDefinition(reseller_id=p.reseller_id, **data.model_dump())
    session.add(row)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise conflict("custom_field_exists", f"Já existe um campo com a chave {data.key}") from exc
    await audit.record(
        session,
        p,
        action="create",
        entity="custom_field",
        entity_id=row.id,
        reseller_id=p.reseller_id,
        after=audit.snapshot(row),
    )
    return row


async def _get(session: AsyncSession, p: Principal, field_id: uuid.UUID) -> CustomFieldDefinition:
    row = await session.get(CustomFieldDefinition, field_id)
    if row is None or row.reseller_id != p.reseller_id:
        raise not_found("Campo personalizado")
    return row


async def update(
    session: AsyncSession, p: Principal, field_id: uuid.UUID, data: CustomFieldUpdate
) -> CustomFieldDefinition:
    p.require("settings.write")
    row = await _get(session, p, field_id)
    before = audit.snapshot(row)
    for k, v in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(row, k, v)
    await session.flush()
    b, a = audit.diff(before, audit.snapshot(row))
    await audit.record(
        session,
        p,
        action="update",
        entity="custom_field",
        entity_id=row.id,
        reseller_id=p.reseller_id,
        before=b,
        after=a,
    )
    return row


async def delete(session: AsyncSession, p: Principal, field_id: uuid.UUID) -> None:
    """Removes the definition; values already stored on devices stay (history), just not shown."""
    p.require("settings.write")
    row = await _get(session, p, field_id)
    snap = audit.snapshot(row)
    await session.delete(row)
    await audit.record(
        session,
        p,
        action="delete",
        entity="custom_field",
        entity_id=field_id,
        reseller_id=p.reseller_id,
        before=snap,
    )


def _coerce(defn: CustomFieldDefinition, value: Any) -> str | float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    match defn.field_type:
        case "number":
            try:
                number = Decimal(str(value).replace(",", "."))
            except InvalidOperation as exc:
                raise bad_request(
                    "invalid_custom_field", f"{defn.label}: número inválido", field=defn.key
                ) from exc
            return float(number)
        case "date":
            try:
                return date.fromisoformat(str(value)).isoformat()
            except ValueError as exc:
                raise bad_request(
                    "invalid_custom_field", f"{defn.label}: data inválida (use AAAA-MM-DD)", field=defn.key
                ) from exc
        case _:
            text = str(value).strip()
            if len(text) > 500:  # noqa: PLR2004
                raise bad_request("invalid_custom_field", f"{defn.label}: até 500 caracteres", field=defn.key)
            return text


async def validate_values(
    session: AsyncSession, reseller_id: uuid.UUID, current: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    """Merges `values` into `current`, accepting only active definitions of the reseller."""
    defs = {d.key: d for d in await list_definitions(session, reseller_id, active_only=True)}
    unknown = sorted(set(values) - set(defs))
    if unknown:
        raise bad_request(
            "unknown_custom_field",
            f"Campos personalizados não cadastrados: {', '.join(unknown)}",
            fields=unknown,
        )
    merged = dict(current)
    for key, raw in values.items():
        value = _coerce(defs[key], raw)
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged
