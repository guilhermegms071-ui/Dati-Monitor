"""Read-only API for the ERP (PROMPT 7): integration tokens and the readings / cutoff / devices queries.
The counters follow exactly the same rules as the reports (app.services.reports.counters)."""

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, not_found, unauthorized
from app.core.principal import Principal
from app.models import Customer, Device, ErpToken, Site, User
from app.schemas.erp import (
    ErpCutoffItem,
    ErpCutoffResponse,
    ErpDevice,
    ErpDevicePage,
    ErpReading,
    ErpReadingPage,
    ErpTokenCreated,
    ErpTokenOut,
)
from app.services import audit
from app.services.reports.base import DISPLAY_TZ, MAX_PERIOD_DAYS
from app.services.reports.counters import cutoff, valid_readings_cte

TOKEN_PREFIX = "dmerp_"  # noqa: S105 - prefixo público do token, não é segredo
# Atualizar last_used_at no máximo uma vez por minuto (a API pode ser chamada em sequência).
LAST_USED_RESOLUTION = timedelta(minutes=1)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ----------------------------------------------------------------------------- tokens (portal)


async def list_tokens(session: AsyncSession, p: Principal) -> list[ErpTokenOut]:
    p.require("integration.read")
    rows = await session.execute(
        select(ErpToken, User.name)
        .outerjoin(User, User.id == ErpToken.created_by)
        .where(ErpToken.reseller_id == p.reseller_id)
        .order_by(ErpToken.revoked_at.is_not(None), ErpToken.created_at.desc())
    )
    return [_out(t, name) for t, name in rows.tuples()]


def _out(t: ErpToken, created_by: str | None) -> ErpTokenOut:
    return ErpTokenOut(
        id=t.id,
        name=t.name,
        token_prefix=t.token_prefix,
        created_at=t.created_at,
        created_by=created_by,
        last_used_at=t.last_used_at,
        revoked_at=t.revoked_at,
    )


async def create_token(session: AsyncSession, p: Principal, name: str) -> ErpTokenCreated:
    """The token is shown ONCE; only its SHA-256 is stored."""
    p.require("integration.create")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ErpToken(
        reseller_id=p.reseller_id,
        name=name.strip(),
        token_prefix=token[: len(TOKEN_PREFIX) + 6],
        token_hash=_hash(token),
        created_by=p.user_id,
    )
    session.add(row)
    await session.flush()
    await audit.record(
        session, p, action="create", entity="erp_token", entity_id=row.id, reseller_id=p.reseller_id,
        after={"name": row.name, "token_prefix": row.token_prefix},
    )  # fmt: skip
    user = await session.get(User, p.user_id)
    return ErpTokenCreated(**_out(row, user.name if user else None).model_dump(), token=token)


async def revoke_token(session: AsyncSession, p: Principal, token_id: uuid.UUID) -> ErpTokenOut:
    p.require("integration.delete")
    row = await session.get(ErpToken, token_id)
    if row is None or row.reseller_id != p.reseller_id:
        raise not_found("Token")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await audit.record(
            session, p, action="revoke", entity="erp_token", entity_id=row.id, reseller_id=p.reseller_id,
            after={"name": row.name},
        )  # fmt: skip
    await session.flush()
    return _out(row, None)


# ----------------------------------------------------------------------------- autenticação do ERP


@dataclass(frozen=True)
class ErpClient:
    token_id: uuid.UUID
    reseller_id: uuid.UUID


async def authenticate(session: AsyncSession, token: str | None) -> ErpClient:
    if not token or not token.startswith(TOKEN_PREFIX):
        raise unauthorized("erp_token_invalid", "Token de integração ausente ou inválido")
    row = (
        await session.execute(select(ErpToken).where(ErpToken.token_hash == _hash(token)))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        raise unauthorized("erp_token_invalid", "Token de integração ausente ou revogado")
    now = datetime.now(UTC)
    if row.last_used_at is None or now - row.last_used_at >= LAST_USED_RESOLUTION:
        await session.execute(update(ErpToken).where(ErpToken.id == row.id).values(last_used_at=now))
        await session.commit()
    return ErpClient(row.id, row.reseller_id)


# ----------------------------------------------------------------------------- consultas


async def _customer_scope(
    session: AsyncSession, client: ErpClient, customer_erp_code: str | None
) -> tuple[str, dict[str, Any]]:
    parts = ["d.deleted_at IS NULL", "d.discovery_state = 'approved'", "d.reseller_id = :erp_reseller"]
    params: dict[str, Any] = {"erp_reseller": client.reseller_id}
    if customer_erp_code is not None:
        customer_id = (
            await session.execute(
                select(Customer.id).where(
                    Customer.reseller_id == client.reseller_id,
                    Customer.erp_code == customer_erp_code,
                    Customer.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if customer_id is None:
            raise not_found("Cliente com este código ERP")
        # Equipamentos que estiveram com o cliente (transferências); as leituras ficam limitadas ao período
        # em que estavam com ele (valid_readings_cte).
        parts.append(
            "(EXISTS (SELECT 1 FROM device_assignments da WHERE da.device_id = d.id"
            " AND da.customer_id = :erp_customer)"
            " OR (NOT EXISTS (SELECT 1 FROM device_assignments dz WHERE dz.device_id = d.id)"
            " AND d.customer_id = :erp_customer))"
        )
        params["erp_customer"] = customer_id
    return " AND ".join(parts), params


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, DISPLAY_TZ).astimezone(UTC)


def _encode_cursor(read_at: datetime, reading_id: uuid.UUID) -> str:
    raw = f"{read_at.isoformat()}|{reading_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        ts, rid = raw.split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(rid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise bad_request("invalid_cursor", "Cursor inválido") from exc


async def readings(
    session: AsyncSession,
    client: ErpClient,
    *,
    customer_erp_code: str | None,
    date_from: date,
    date_to: date,
    cursor: str | None,
    limit: int,
) -> ErpReadingPage:
    """Valid readings (adjustments applied, regressions left out) read between the two days, oldest first."""
    if date_from > date_to:
        raise bad_request("invalid_period", "`from` é depois de `to`")
    if (date_to - date_from).days + 1 > MAX_PERIOD_DAYS:
        raise bad_request("period_too_long", f"Período máximo: {MAX_PERIOD_DAYS} dias")
    scope, params = await _customer_scope(session, client, customer_erp_code)
    lo, hi = _day_start(date_from), _day_start(date_to + timedelta(days=1))
    after = ""
    if cursor:
        after_at, after_id = _decode_cursor(cursor)
        after = "AND (e.read_at, e.reading_id) > (:after_at, :after_id)"
        params |= {"after_at": after_at, "after_id": after_id}
    sql = text(
        f"""
        WITH {valid_readings_cte(scope, params)}
        SELECT e.reading_id, e.read_at, e.device_id, d.serial, c.erp_code,
               e.total, e.mono, e.color, e.adjusted
        FROM eff e
        JOIN devices d ON d.id = e.device_id
        LEFT JOIN LATERAL (
            SELECT a.customer_id FROM device_assignments a
            WHERE a.device_id = e.device_id AND e.read_at >= a.start_at
              AND (a.end_at IS NULL OR e.read_at < a.end_at)
            ORDER BY a.start_at DESC LIMIT 1
        ) owner ON true
        JOIN customers c ON c.id = coalesce(owner.customer_id, d.customer_id)
        WHERE e.read_at >= :lo {after}
        ORDER BY e.read_at, e.reading_id
        LIMIT :limit
        """  # noqa: S608 - fragmentos fixos; valores por parâmetro
    )
    rows = (
        await session.execute(sql, {**params, "lo": lo, "hi": hi, "lo_base": lo, "limit": limit + 1})
    ).all()
    items = [
        ErpReading(
            reading_id=r[0],
            read_at=r[1],
            device_id=r[2],
            serial=r[3],
            customer_erp_code=r[4],
            total=r[5],
            mono=r[6],
            color=r[7],
            adjusted=r[8],
        )
        for r in rows[:limit]
    ]
    nxt = _encode_cursor(items[-1].read_at, items[-1].reading_id) if len(rows) > limit and items else None
    return ErpReadingPage(items=items, next_cursor=nxt)


async def cutoff_readings(
    session: AsyncSession, client: ErpClient, *, cutoff_date: date, customer_erp_code: str | None
) -> ErpCutoffResponse:
    scope, params = await _customer_scope(session, client, customer_erp_code)
    before = _day_start(cutoff_date + timedelta(days=1))
    got = await cutoff(session, scope, params, before)
    devices = (
        await session.execute(
            select(Device.id, Device.serial, Customer.erp_code)
            .join(Customer, Customer.id == Device.customer_id)
            .where(Device.id.in_(list(got)))
        )
    ).all() if got else []  # fmt: skip
    info = {d[0]: (d[1], customer_erp_code or d[2]) for d in devices}
    items = [
        ErpCutoffItem(
            device_id=c.device_id,
            serial=info[c.device_id][0],
            customer_erp_code=info[c.device_id][1],
            reading_id=c.reading_id,
            read_at=c.read_at,
            total=c.total,
            mono=c.mono,
            color=c.color,
        )
        for c in sorted(got.values(), key=lambda c: info[c.device_id][0])
    ]
    return ErpCutoffResponse(date=cutoff_date, items=items)


async def devices(
    session: AsyncSession,
    client: ErpClient,
    *,
    customer_erp_code: str | None,
    after: uuid.UUID | None,
    limit: int,
) -> ErpDevicePage:
    stmt = (
        select(Device, Customer.erp_code, Customer.name, Site.name)
        .join(Customer, Customer.id == Device.customer_id)
        .join(Site, Site.id == Device.site_id)
        .where(
            Device.reseller_id == client.reseller_id,
            Device.deleted_at.is_(None),
            Device.discovery_state == "approved",
        )
        .order_by(Device.id)
        .limit(limit + 1)
    )
    if customer_erp_code is not None:
        await _customer_scope(session, client, customer_erp_code)  # 404 se o código não existe
        stmt = stmt.where(Customer.erp_code == customer_erp_code)
    if after is not None:
        stmt = stmt.where(Device.id > after)
    rows = (await session.execute(stmt)).tuples().all()
    items = [
        ErpDevice(
            device_id=d.id,
            serial=d.serial,
            alt_serial=d.alt_serial,
            asset_tag=d.asset_tag,
            brand=d.brand,
            model=d.model,
            is_color=d.is_color,
            sector=d.sector,
            active=d.active,
            customer_erp_code=erp,
            customer_name=cname,
            site_name=sname,
            franchise_value=d.franchise_value,
            franchise_pages_mono=d.franchise_pages_mono,
            franchise_pages_color=d.franchise_pages_color,
            overage_price_mono=d.overage_price_mono,
            overage_price_color=d.overage_price_color,
            last_read_at=d.last_read_at,
            last_total=d.last_total,
            last_mono=d.last_mono,
            last_color=d.last_color,
        )
        for d, erp, cname, sname in rows[:limit]
    ]
    return ErpDevicePage(items=items, next_after=items[-1].device_id if len(rows) > limit else None)
