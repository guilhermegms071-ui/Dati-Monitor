"""Ingestion of agent batches (POST /api/agent/readings): per-item idempotency, identity by serial,
reading validations (PROMPT 6.5), cluster anti-duplication (4.8), supplies and status."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Agent,
    Brand,
    Device,
    DeviceEvent,
    Reading,
    ReadingDiscard,
    ReadingIdempotency,
    Setting,
    Site,
    SupplyCurrent,
    SupplyReading,
)
from app.models.readings import COUNTER_FIELDS
from app.schemas import agent as proto
from app.services.agents import collection_settings
from app.services.alerts import open_alert
from app.services.catalog import brand_name

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES_PER_DAY = 50_000
MAX_PAGES_KEY = "validation.max_pages_per_day"
FUTURE_TOLERANCE = timedelta(minutes=5)


class ItemRejectedError(Exception):
    pass


@dataclass
class IngestContext:
    agent: Agent
    site: Site
    now: datetime
    max_pages_per_day: int
    counters_interval: timedelta


async def build_context(session: AsyncSession, agent: Agent) -> IngestContext:
    site = await session.get(Site, agent.site_id)
    if site is None:
        raise ItemRejectedError("local do coletor não existe mais")
    setting = (
        await session.execute(
            select(Setting.value).where(
                Setting.reseller_id == agent.reseller_id, Setting.key == MAX_PAGES_KEY
            )
        )
    ).scalar_one_or_none()
    max_pages = int(setting["value"]) if setting and "value" in setting else DEFAULT_MAX_PAGES_PER_DAY
    minutes = int(collection_settings(site)["counters_minutes"])
    return IngestContext(agent, site, datetime.now(UTC), max_pages, timedelta(minutes=minutes))


async def ingest_batch(
    session: AsyncSession, agent: Agent, req: proto.ReadingsRequest
) -> proto.ReadingsResponse:
    ctx = await build_context(session, agent)
    results: list[proto.ItemResult] = []
    for item in req.items:
        try:
            async with session.begin_nested():
                status = await _ingest_item(session, ctx, item)
            results.append(proto.ItemResult(key=item.key, status=status))
        except ItemRejectedError as exc:
            logger.warning("item %s do coletor %s rejeitado: %s", item.key, agent.id, exc)
            results.append(proto.ItemResult(key=item.key, status="rejected", reason=str(exc)))
    return proto.ReadingsResponse(results=results)


async def _claim_key(session: AsyncSession, ctx: IngestContext, item: proto.Item, read_at: datetime) -> bool:
    if not item.key.startswith(f"{ctx.agent.id}:"):
        raise ItemRejectedError("chave de idempotência não pertence a este coletor")
    row = await session.execute(
        insert(ReadingIdempotency)
        .values(idempotency_key=item.key, kind=item.kind, agent_id=ctx.agent.id, read_at=read_at)
        .on_conflict_do_nothing(index_elements=[ReadingIdempotency.idempotency_key])
        .returning(ReadingIdempotency.idempotency_key)
    )
    return row.first() is not None


def _normalize_read_at(ctx: IngestContext, item: proto.Item) -> tuple[datetime, bool]:
    read_at = item.read_at if item.read_at.tzinfo else item.read_at.replace(tzinfo=UTC)
    if read_at > ctx.now + FUTURE_TOLERANCE:
        # Relógio adiantado: a leitura entra com a hora de recebimento (a original fica no extra).
        return ctx.now, True
    return read_at, False


async def _ingest_item(session: AsyncSession, ctx: IngestContext, item: proto.Item) -> str:
    read_at, clamped = _normalize_read_at(ctx, item)
    if not await _claim_key(session, ctx, item, read_at):
        return "duplicate"
    missing = ItemRejectedError(f"item do tipo {item.kind} sem o conteúdo correspondente")
    if item.kind == "reading":
        if item.reading is None:
            raise missing
        device = await resolve_device(session, ctx, item.device, read_at)
        return await _reading(
            session, ctx, item=item, payload=item.reading, device=device, read_at=read_at, clamped=clamped
        )
    if item.kind == "supplies":
        if item.supplies is None:
            raise missing
        device = await resolve_device(session, ctx, item.device, read_at)
        await _supplies(session, device, item.supplies, read_at)
    elif item.kind == "status":
        if item.status is None:
            raise missing
        device = await resolve_device(session, ctx, item.device, read_at)
        _status(device, item.status, read_at)
    else:
        if item.event is None:
            raise missing
        device = await resolve_device(session, ctx, item.device, read_at)
        await _event(session, ctx, device, item.event, read_at)
    return "accepted"


# ----------------------------------------------------------------------------- identity


async def _event_row(
    session: AsyncSession, device: Device, type_: str, data: dict[str, Any], user_id: uuid.UUID | None = None
) -> None:
    session.add(
        DeviceEvent(
            reseller_id=device.reseller_id, device_id=device.id, type=type_, data=data, user_id=user_id
        )
    )


async def _brand_id(session: AsyncSession, name: str | None) -> uuid.UUID | None:
    if not name:
        return None
    return (await session.execute(select(Brand.id).where(Brand.name == name))).scalar_one_or_none()


async def _release_ip(session: AsyncSession, ctx: IngestContext, device: Device, ip: str, port: int) -> None:
    """Another active device of the site answered on this IP before: it was replaced (PROMPT 4.6)."""
    if not ip:
        return
    others = (
        await session.execute(
            select(Device).where(
                Device.site_id == ctx.site.id,
                Device.ip == ip,
                Device.snmp_port == port,
                Device.id != device.id,
                Device.deleted_at.is_(None),
            )
        )
    ).scalars()
    for old in others:
        await _event_row(
            session,
            old,
            "replaced",
            {"ip": ip, "port": port, "new_serial": device.serial, "new_device_id": str(device.id)},
        )
        old.ip = None


async def resolve_device(
    session: AsyncSession, ctx: IngestContext, ref: proto.DeviceRef, read_at: datetime
) -> Device:
    serial = ref.serial.strip()
    if not serial:
        raise ItemRejectedError("equipamento sem número de série")
    device = (
        await session.execute(
            select(Device)
            .where(Device.reseller_id == ctx.agent.reseller_id, Device.serial == serial)
            .with_for_update()
        )
    ).scalar_one_or_none()
    brand = brand_name(ref.sys_object_id)
    if device is None:
        device = Device(
            id=uuid.uuid4(),
            reseller_id=ctx.agent.reseller_id,
            site_id=ctx.site.id,
            customer_id=ctx.site.customer_id,
            serial=serial,
            ip=ref.ip or None,
            snmp_port=ref.port,
            mac=ref.mac,
            hostname=ref.hostname,
            sys_object_id=ref.sys_object_id,
            sys_descr=ref.sys_descr,
            model=ref.model,
            firmware=ref.firmware,
            brand=brand,
            brand_id=await _brand_id(session, brand),
            profile_key=ref.profile_key,
            first_seen_at=min(read_at, ctx.now),
            last_agent_id=ctx.agent.id,
            source="snmp",
        )
        session.add(device)
        await session.flush()
        await _event_row(
            session, device, "discovered", {"ip": ref.ip, "port": ref.port, "agent_id": str(ctx.agent.id)}
        )
        await _release_ip(session, ctx, device, ref.ip, ref.port)
        return device
    if device.deleted_at is not None:
        device.deleted_at = None
        await _event_row(
            session, device, "reactivated", {"reason": "voltou a responder", "agent_id": str(ctx.agent.id)}
        )
    if device.site_id != ctx.site.id:
        await _event_row(
            session,
            device,
            "moved_site",
            {
                "from_site_id": str(device.site_id),
                "to_site_id": str(ctx.site.id),
                "agent_id": str(ctx.agent.id),
            },
        )
        device.site_id, device.customer_id = ctx.site.id, ctx.site.customer_id
    if ref.ip and (ref.ip != device.ip or ref.port != device.snmp_port):
        await _event_row(
            session,
            device,
            "ip_changed",
            {"old_ip": device.ip, "old_port": device.snmp_port, "new_ip": ref.ip, "new_port": ref.port},
        )
        device.ip, device.snmp_port = ref.ip, ref.port
        await _release_ip(session, ctx, device, ref.ip, ref.port)
    for attr in ("mac", "hostname", "sys_object_id", "sys_descr", "model", "firmware", "profile_key"):
        value = getattr(ref, attr)
        if value:
            setattr(device, attr, value)
    if brand and device.brand != brand:
        device.brand, device.brand_id = brand, await _brand_id(session, brand)
    device.last_agent_id = ctx.agent.id
    return device


# ----------------------------------------------------------------------------- readings


def _counter(payload: proto.ReadingPayload, name: str) -> int | None:
    v = payload.counters.get(name)
    return int(v) if v is not None else None


async def _previous(session: AsyncSession, device_id: uuid.UUID, read_at: datetime) -> Reading | None:
    return (
        await session.execute(
            select(Reading)
            .where(Reading.device_id == device_id, Reading.read_at < read_at)
            .order_by(Reading.read_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _nearest_other_agent(
    session: AsyncSession, ctx: IngestContext, device_id: uuid.UUID, read_at: datetime
) -> Reading | None:
    window = ctx.counters_interval / 2
    return (
        await session.execute(
            select(Reading)
            .where(
                Reading.device_id == device_id,
                Reading.agent_id != ctx.agent.id,
                Reading.read_at > read_at - window,
                Reading.read_at < read_at + window,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


def _validate(
    ctx: IngestContext,
    prev: Reading | None,
    values: dict[str, int | None],
    tolerance: float,
    read_at: datetime,
) -> tuple[list[str], dict[str, Any]]:
    """PROMPT 6.5: regression (never rejected, only flagged), absurd jump and PB+color ≠ total."""
    flags: list[str] = []
    data: dict[str, Any] = {}
    if prev is not None:
        regressed: list[str] = []
        for f in ("total", "mono", "color"):
            now_v, before = values[f], getattr(prev, f)
            if now_v is not None and before is not None and now_v < before:
                regressed.append(f)
        if regressed:
            flags.append("counter_regression")
            data["regression"] = {f: {"before": getattr(prev, f), "after": values[f]} for f in regressed}
        total, prev_total = values["total"], prev.total
        if total is not None and prev_total is not None and total > prev_total:
            days = max((read_at - prev.read_at).total_seconds() / 86400, 1.0)
            per_day = (total - prev_total) / days
            if per_day > ctx.max_pages_per_day:
                flags.append("suspicious_jump")
                data["jump"] = {"pages_per_day": round(per_day), "limit": ctx.max_pages_per_day}
    total, mono, color = values["total"], values["mono"], values["color"]
    if total and mono is not None and color is not None:
        diff_pct = abs(mono + color - total) / total * 100
        if diff_pct > tolerance:
            flags.append("sum_mismatch")
            data["sum_mismatch"] = {
                "total": total,
                "mono": mono,
                "color": color,
                "diff_percent": round(diff_pct, 2),
            }
    return flags, data


async def _discard(
    session: AsyncSession,
    ctx: IngestContext,
    *,
    item: proto.Item,
    payload: proto.ReadingPayload,
    device: Device,
    read_at: datetime,
    other: Reading,
) -> str:
    session.add(
        ReadingDiscard(
            reseller_id=ctx.agent.reseller_id,
            device_id=device.id,
            agent_id=ctx.agent.id,
            read_at=read_at,
            idempotency_key=item.key,
            reason="duplicate_other_agent",
            payload={
                "other_reading_id": str(other.id),
                "other_agent_id": str(other.agent_id),
                "counters": payload.counters,
            },
        )
    )
    await _event_row(
        session, device, "reading_discarded", {"reason": "duplicate_other_agent", "key": item.key}
    )
    await session.execute(
        update(ReadingIdempotency)
        .where(ReadingIdempotency.idempotency_key == item.key)
        .values(result="discarded")
    )
    return "discarded"


async def _reading(
    session: AsyncSession,
    ctx: IngestContext,
    *,
    item: proto.Item,
    payload: proto.ReadingPayload,
    device: Device,
    read_at: datetime,
    clamped: bool,
) -> str:
    # Anti-duplicidade do cluster (PROMPT 4.8): outro coletor já leu este equipamento neste intervalo.
    other = await _nearest_other_agent(session, ctx, device.id, read_at)
    if other is not None:
        return await _discard(
            session, ctx, item=item, payload=payload, device=device, read_at=read_at, other=other
        )

    values = {f: _counter(payload, f) for f in COUNTER_FIELDS}
    extra: dict[str, Any] = {
        "counters": payload.counters,
        "raw": payload.extra,
        "profile_version": payload.profile_version,
        "mono_only": payload.mono_only,
        "unresolved": payload.unresolved,
    }
    prev = await _previous(session, device.id, read_at)
    flags, alert_data = _validate(ctx, prev, values, payload.sum_tolerance_percent, read_at)
    if clamped:
        extra["original_read_at"] = item.read_at.isoformat()
        flags.append("future_read_at")
    total, mono, color = values["total"], values["mono"], values["color"]
    reading = Reading(
        id=uuid.uuid4(),
        read_at=read_at,
        received_at=ctx.now,
        reseller_id=ctx.agent.reseller_id,
        device_id=device.id,
        agent_id=ctx.agent.id,
        idempotency_key=item.key,
        extra=extra,
        status=payload.status,
        error_bits=payload.error_bits,
        source=payload.source,
        profile_key=payload.profile_key or None,
        counter_source=payload.counter_source or None,
        flags=flags,
        **values,
    )
    session.add(reading)
    await session.flush()
    await session.execute(
        update(ReadingIdempotency)
        .where(ReadingIdempotency.idempotency_key == item.key)
        .values(reading_id=reading.id)
    )
    await _flag_side_effects(session, device=device, reading=reading, flags=flags, data=alert_data, prev=prev)
    # Estado "atual" do equipamento só avança com a leitura mais recente (filas antigas chegam depois).
    if device.last_read_at is None or read_at >= device.last_read_at:
        device.last_read_at = read_at
        device.last_total, device.last_mono, device.last_color = total, mono, color
        device.counter_source = payload.counter_source or device.counter_source
        device.disconnected = False
        if payload.status:
            device.last_status, device.last_status_at = payload.status, read_at
            device.last_error_bits = payload.error_bits
    if payload.mono_only:
        device.is_color = False
    elif color is not None and color > 0:
        device.is_color = True
    return "accepted"


async def _flag_side_effects(
    session: AsyncSession,
    *,
    device: Device,
    reading: Reading,
    flags: list[str],
    data: dict[str, Any],
    prev: Reading | None,
) -> None:
    base = {"reading_id": str(reading.id), "read_at": reading.read_at.isoformat()}
    if "counter_regression" in flags:
        detail = {**base, **data["regression"], "previous_reading_id": str(prev.id) if prev else None}
        await _event_row(session, device, "counter_regression", detail)
        await open_alert(
            session,
            reseller_id=device.reseller_id,
            customer_id=device.customer_id,
            site_id=device.site_id,
            type_="counter_regression",
            severity="warning",
            target_type="device",
            target_id=device.id,
            message=f"Contador regrediu no equipamento {device.serial}",
            dedup_key=f"counter_regression:{reading.id}",
            data=detail,
        )
    if "suspicious_jump" in flags:
        detail = {**base, **data["jump"]}
        await _event_row(session, device, "suspicious_jump", detail)
        await open_alert(
            session,
            reseller_id=device.reseller_id,
            customer_id=device.customer_id,
            site_id=device.site_id,
            type_="suspicious_jump",
            severity="warning",
            target_type="device",
            target_id=device.id,
            message=(
                f"Salto suspeito de contador no equipamento {device.serial} "
                f"({data['jump']['pages_per_day']} páginas/dia)"
            ),
            dedup_key=f"suspicious_jump:{reading.id}",
            data=detail,
        )
    if "sum_mismatch" in flags:
        detail = {**base, **data["sum_mismatch"]}
        await _event_row(session, device, "sum_mismatch", detail)
        await open_alert(
            session,
            reseller_id=device.reseller_id,
            customer_id=device.customer_id,
            site_id=device.site_id,
            type_="sum_mismatch",
            severity="info",
            target_type="device",
            target_id=device.id,
            message=f"PB + cor diferente do total no equipamento {device.serial}: revisar o perfil",
            dedup_key=f"sum_mismatch:{device.id}",
            data=detail,
        )


# ----------------------------------------------------------------------------- supplies / status / events


async def _supplies(
    session: AsyncSession, device: Device, supplies: list[proto.Supply], read_at: datetime
) -> None:
    for s in supplies:
        percent = Decimal(str(round(s.percent, 2))) if s.percent is not None else None
        fields = {
            "description": s.description or None,
            "supply_type": s.type,
            "supply_class": s.class_,
            "color": s.color or None,
            "level": s.level,
            "max_capacity": s.max_capacity,
            "percent": percent,
            "level_state": s.level_state,
            "unit": s.unit,
        }
        session.add(
            SupplyReading(
                read_at=read_at,
                reseller_id=device.reseller_id,
                device_id=device.id,
                supply_key=s.key,
                **fields,
            )
        )
        stmt = insert(SupplyCurrent).values(
            device_id=device.id, supply_key=s.key, reseller_id=device.reseller_id, read_at=read_at, **fields
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[SupplyCurrent.device_id, SupplyCurrent.supply_key],
            set_={**fields, "read_at": read_at, "updated_at": func.now()},
            where=SupplyCurrent.read_at <= read_at,
        )
        await session.execute(stmt)
    if device.last_read_at is None:
        device.last_read_at = read_at


def _status(device: Device, st: proto.StatusPayload, read_at: datetime) -> None:
    if device.last_status_at is not None and read_at < device.last_status_at:
        return  # status antigo chegando depois (fila): não sobrescreve o atual
    device.last_status, device.last_status_at = st.status, read_at
    device.last_error_bits = st.error_bits
    device.last_error_reasons = st.reasons
    device.last_panel_text = st.panel_text
    device.disconnected = False
    if device.last_read_at is None or read_at > device.last_read_at:
        device.last_read_at = read_at


async def _event(
    session: AsyncSession, ctx: IngestContext, device: Device, ev: proto.EventPayload, read_at: datetime
) -> None:
    await _event_row(
        session, device, ev.type, {**ev.data, "agent_id": str(ctx.agent.id), "at": read_at.isoformat()}
    )
    if ev.type == "read_failed" and (device.last_status_at is None or read_at >= device.last_status_at):
        device.last_status, device.last_status_at = "offline", read_at
