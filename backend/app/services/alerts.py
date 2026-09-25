"""Opening and resolving alerts with de-duplication (one non-resolved alert per dedup key)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert


async def open_alert(
    session: AsyncSession,
    *,
    reseller_id: uuid.UUID,
    type_: str,
    severity: str,
    target_type: str,
    target_id: uuid.UUID,
    message: str,
    dedup_key: str,
    customer_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    rule_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    """Opens the alert unless an unresolved one with the same dedup key exists. Returns the new id."""
    stmt = (
        insert(Alert)
        .values(
            id=uuid.uuid4(),
            reseller_id=reseller_id,
            customer_id=customer_id,
            site_id=site_id,
            rule_id=rule_id,
            type=type_,
            severity=severity,
            target_type=target_type,
            target_id=target_id,
            state="open",
            message=message,
            dedup_key=dedup_key,
            data=data or {},
        )
        .on_conflict_do_nothing(index_elements=[Alert.dedup_key], index_where=text("state <> 'resolved'"))
        .returning(Alert.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def resolve_alerts(session: AsyncSession, dedup_key: str, *, user_id: uuid.UUID | None = None) -> int:
    res = await session.execute(
        update(Alert)
        .where(Alert.dedup_key == dedup_key, Alert.state != "resolved")
        .values(state="resolved", resolved_at=datetime.now(UTC), resolved_by=user_id)
        .returning(Alert.id)
    )
    return len(res.all())


async def open_alert_exists(session: AsyncSession, dedup_key: str) -> bool:
    row = await session.execute(
        select(Alert.id).where(Alert.dedup_key == dedup_key, Alert.state != "resolved").limit(1)
    )
    return row.first() is not None
