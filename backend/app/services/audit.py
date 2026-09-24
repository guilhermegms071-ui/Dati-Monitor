"""Audit log: every write action from the portal is recorded in the same transaction (PROMPT 3/12)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.models import AuditLog

# Nunca gravar segredos na auditoria.
_SECRET_FIELDS = frozenset(
    {
        "password_hash",
        "totp_secret_enc",
        "secret_hash",
        "community_enc",
        "v3_auth_password_enc",
        "v3_priv_password_enc",
        "config_enc",
        "value_enc",
        "token_hash",
    }
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return "<binário>"
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    return value


def snapshot(obj: object) -> dict[str, Any]:
    """Column values of an ORM object, JSON-safe and without secrets."""
    state = inspect(obj)
    if state is None:
        raise TypeError(f"{type(obj).__name__} não é um objeto ORM")
    mapper = state.mapper
    return {
        attr.key: _json_safe(getattr(obj, attr.key))
        for attr in mapper.column_attrs
        if attr.key not in _SECRET_FIELDS
    }


def diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    changed = {k for k in after if before.get(k) != after.get(k) and k != "updated_at"}
    return {k: before.get(k) for k in changed}, {k: after.get(k) for k in changed}


async def record(
    session: AsyncSession,
    principal: Principal | None,
    *,
    action: str,
    entity: str,
    entity_id: uuid.UUID | str | None,
    reseller_id: uuid.UUID | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            reseller_id=reseller_id,
            user_id=principal.user_id if principal else None,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            before=_json_safe(before) if before is not None else None,
            after=_json_safe(after) if after is not None else None,
            ip=principal.ip if principal else None,
        )
    )
