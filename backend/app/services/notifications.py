"""Notifications (PROMPT 9): channels with encrypted credentials, the queue fed by new alerts, delivery
with retry/backoff, quiet hours (non-critical waits for the end of the window), channel test and the
daily summary. Every attempt is recorded in `notifications` (visible in Alertas > Notificações)."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import Settings
from app.core.errors import bad_request, not_found
from app.core.principal import Principal
from app.models import (
    Agent,
    Alert,
    Device,
    Notification,
    NotificationChannel,
    Reseller,
    Setting,
    SupplyCurrent,
)
from app.schemas.alerts import (
    RULE_LABELS,
    SECRET_KEYS,
    ChannelIn,
    ChannelOut,
    ChannelTestOut,
    ChannelUpdate,
    NotificationSettings,
)
from app.services import audit
from app.services.alert_rules import ensure_default_rules, load_rules
from app.services.notifiers import Message, NotifyError, build_notifier

logger = logging.getLogger(__name__)

SP = ZoneInfo("America/Sao_Paulo")
SETTINGS_KEY = "notifications"
MASK = "••••"
SEVERITY_LABEL = {"critical": "CRÍTICO", "warning": "Atenção", "info": "Aviso"}
BACKOFF_MINUTES = (1, 2, 5, 15, 30, 60)
CRITICAL_PERCENT = 10  # resumo diário: toner crítico
CRITICAL_DAYS = 7


# ----------------------------------------------------------------------------- configurações


async def get_settings(session: AsyncSession, reseller_id: uuid.UUID) -> NotificationSettings:
    value = (
        await session.execute(
            select(Setting.value).where(Setting.reseller_id == reseller_id, Setting.key == SETTINGS_KEY)
        )
    ).scalar_one_or_none()
    return NotificationSettings.model_validate(value or {})


async def put_settings(
    session: AsyncSession, p: Principal, data: NotificationSettings
) -> NotificationSettings:
    p.require("notifications.write")
    before = await get_settings(session, p.reseller_id)
    value = data.model_dump()
    await session.execute(
        insert(Setting)
        .values(reseller_id=p.reseller_id, key=SETTINGS_KEY, value=value)
        .on_conflict_do_update(index_elements=[Setting.reseller_id, Setting.key], set_={"value": value})
    )
    # O limiar de troca de suprimento (16.3) é lido pela ingestão na chave própria.
    threshold = {"value": data.replacement_threshold_points}
    await session.execute(
        insert(Setting)
        .values(reseller_id=p.reseller_id, key="supplies.replacement_threshold_points", value=threshold)
        .on_conflict_do_update(index_elements=[Setting.reseller_id, Setting.key], set_={"value": threshold})
    )
    await audit.record(
        session,
        p,
        action="update",
        entity="notification_settings",
        entity_id=None,
        reseller_id=p.reseller_id,
        before=before.model_dump(),
        after=value,
    )
    return data


def quiet_until(now: datetime, s: NotificationSettings) -> datetime | None:
    """End of the quiet window if `now` is inside it (São Paulo time), else None."""
    q = s.quiet_hours
    if not q.enabled or q.start_hour == q.end_hour:
        return None
    local = now.astimezone(SP)
    h = local.hour
    inside = (
        (q.start_hour <= h or h < q.end_hour)
        if q.start_hour > q.end_hour
        else (q.start_hour <= h < q.end_hour)
    )
    if not inside:
        return None
    end = local.replace(hour=q.end_hour, minute=0, second=0, microsecond=0)
    if end <= local:
        end += timedelta(days=1)
    return end.astimezone(UTC)


# ----------------------------------------------------------------------------- canais


def _aad(channel_id: uuid.UUID) -> bytes:
    return b"channel:" + channel_id.bytes


def channel_config(settings: Settings, ch: NotificationChannel) -> dict[str, Any]:
    if not ch.config_enc:
        return {}
    return crypto.decrypt_json(settings.master_key_bytes, ch.config_enc, associated_data=_aad(ch.id))


def _masked(config: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in config.items():
        if k in SECRET_KEYS and v:
            out[k] = MASK
        elif k == "headers" and isinstance(v, dict):
            out[k] = dict.fromkeys(v, MASK)
        else:
            out[k] = v
    return out


def channel_out(settings: Settings, ch: NotificationChannel) -> ChannelOut:
    return ChannelOut(
        id=ch.id,
        kind=ch.kind,
        name=ch.name,
        config=_masked(channel_config(settings, ch)),
        recipients=[str(r) for r in ch.recipients],
        enabled=ch.enabled,
        created_at=ch.created_at,
        updated_at=ch.updated_at,
    )


def _validate(kind: str, config: dict[str, Any], recipients: list[str]) -> None:
    if kind == "webhook":
        url = str(config.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise bad_request("invalid_channel", "Webhook precisa de uma URL http(s)")
        return
    if not recipients:
        raise bad_request("invalid_channel", "Informe ao menos um destinatário")
    if kind == "email":
        bad = [r for r in recipients if "@" not in r]
        if bad:
            raise bad_request("invalid_channel", f"E-mail inválido: {', '.join(bad)}")
    if kind == "whatsapp":
        provider = config.get("provider", "meta")
        if provider == "meta" and not (config.get("phone_number_id") and config.get("access_token")):
            raise bad_request("invalid_channel", "WhatsApp (Meta) precisa de phone_number_id e access_token")
        if provider == "generic" and not str(config.get("url_template") or "").startswith(
            ("http://", "https://")
        ):
            raise bad_request("invalid_channel", "WhatsApp (genérico) precisa de url_template http(s)")
        if provider not in ("meta", "generic"):
            raise bad_request("invalid_channel", "Provedor de WhatsApp: meta ou generic")


async def list_channels(session: AsyncSession, settings: Settings, p: Principal) -> list[ChannelOut]:
    p.require("alerts.read")
    rows = (
        await session.execute(
            select(NotificationChannel)
            .where(NotificationChannel.reseller_id == p.reseller_id)
            .order_by(NotificationChannel.name)
        )
    ).scalars()
    return [channel_out(settings, c) for c in rows]


async def create_channel(
    session: AsyncSession, settings: Settings, p: Principal, data: ChannelIn
) -> ChannelOut:
    p.require("notifications.write")
    _validate(data.kind, data.config, data.recipients)
    ch = NotificationChannel(
        id=uuid.uuid4(),
        reseller_id=p.reseller_id,
        kind=data.kind,
        name=data.name,
        recipients=data.recipients,
        enabled=data.enabled,
    )
    ch.config_enc = crypto.encrypt_json(settings.master_key_bytes, data.config, associated_data=_aad(ch.id))
    session.add(ch)
    await session.flush()
    await audit.record(
        session,
        p,
        action="create",
        entity="notification_channel",
        entity_id=ch.id,
        reseller_id=p.reseller_id,
        after={"kind": ch.kind, "name": ch.name, "recipients": ch.recipients, "config": _masked(data.config)},
    )
    return channel_out(settings, ch)


async def _channel(session: AsyncSession, p: Principal, channel_id: uuid.UUID) -> NotificationChannel:
    ch = await session.get(NotificationChannel, channel_id)
    if ch is None or ch.reseller_id != p.reseller_id:
        raise not_found("Canal")
    return ch


async def update_channel(
    session: AsyncSession, settings: Settings, p: Principal, channel_id: uuid.UUID, data: ChannelUpdate
) -> ChannelOut:
    p.require("notifications.write")
    ch = await _channel(session, p, channel_id)
    config = channel_config(settings, ch)
    if data.config is not None:
        merged = {**config}
        for k, v in data.config.items():
            if k in SECRET_KEYS and (v == MASK or v is None):
                continue  # segredo não reenviado: mantém o guardado
            if k == "headers" and isinstance(v, dict):
                old = config.get("headers") or {}
                merged[k] = {hk: (old.get(hk, "") if hv == MASK else hv) for hk, hv in v.items()}
                continue
            merged[k] = v
        config = merged
    recipients = data.recipients if data.recipients is not None else [str(r) for r in ch.recipients]
    recipients = [r.strip() for r in recipients if r.strip()]
    _validate(ch.kind, config, recipients)
    ch.config_enc = crypto.encrypt_json(settings.master_key_bytes, config, associated_data=_aad(ch.id))
    ch.recipients = recipients
    if data.name is not None:
        ch.name = data.name
    if data.enabled is not None:
        ch.enabled = data.enabled
    await session.flush()
    await audit.record(
        session,
        p,
        action="update",
        entity="notification_channel",
        entity_id=ch.id,
        reseller_id=p.reseller_id,
        after={"name": ch.name, "recipients": recipients, "enabled": ch.enabled, "config": _masked(config)},
    )
    return channel_out(settings, ch)


async def delete_channel(session: AsyncSession, p: Principal, channel_id: uuid.UUID) -> None:
    p.require("notifications.write")
    ch = await _channel(session, p, channel_id)
    await session.execute(
        update(Notification).where(Notification.channel_id == ch.id).values(channel_id=None)
    )
    await session.delete(ch)
    await audit.record(
        session,
        p,
        action="delete",
        entity="notification_channel",
        entity_id=channel_id,
        reseller_id=p.reseller_id,
        before={"kind": ch.kind, "name": ch.name},
    )


def destinations(ch: NotificationChannel, config: dict[str, Any]) -> list[str]:
    if ch.kind == "webhook":
        return [str(config.get("url") or "")]
    return [str(r) for r in ch.recipients]


async def test_channel(
    session: AsyncSession, settings: Settings, p: Principal, channel_id: uuid.UUID, client: httpx.AsyncClient
) -> ChannelTestOut:
    """Sends a test message now to every destination and records each attempt."""
    p.require("notifications.write")
    ch = await _channel(session, p, channel_id)
    msg = Message(
        subject="[Dati Monitor] Teste de notificação",
        body=f'Mensagem de teste do canal "{ch.name}", enviada por {p.email}.',
        payload={"type": "test", "channel": ch.name},
    )
    results: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    config = channel_config(settings, ch)
    for dest in destinations(ch, config):
        row = Notification(
            reseller_id=ch.reseller_id,
            channel_id=ch.id,
            kind=ch.kind,
            destination=dest[:500],
            subject=msg.subject,
            body=msg.body,
            attempts=1,
            next_attempt_at=now,
        )
        try:
            await build_notifier(ch.kind, config, settings, client).send(dest, msg)
            row.status, row.sent_at = "sent", now
            results.append({"destination": dest, "ok": True})
        except NotifyError as exc:
            row.status, row.error = "failed", str(exc)
            logger.error("teste do canal %s para %s falhou: %s", ch.name, dest, exc)
            results.append({"destination": dest, "ok": False, "error": str(exc)})
        session.add(row)
    await audit.record(
        session,
        p,
        action="test",
        entity="notification_channel",
        entity_id=ch.id,
        reseller_id=ch.reseller_id,
        after={"results": results},
    )
    return ChannelTestOut(ok=all(r["ok"] for r in results) and bool(results), results=results)


# ----------------------------------------------------------------------------- fila


def _alert_message(settings: Settings, alert: Alert) -> Message:
    label = RULE_LABELS.get(alert.type, alert.type)
    subject = (
        f"[Dati Monitor] {SEVERITY_LABEL.get(alert.severity, alert.severity)} — {label}: {alert.message}"
    )
    opened = alert.opened_at.astimezone(SP).strftime("%d/%m/%Y %H:%M")
    body = (
        f"{alert.message}\n\nTipo: {label}\nGravidade: {SEVERITY_LABEL.get(alert.severity, alert.severity)}\n"
        f"Aberto em: {opened} (horário de Brasília)\n\n"
        f"Ver no portal: {settings.public_base_url.rstrip('/')}/alertas?alerta={alert.id}"
    )
    payload = {
        "type": "alert",
        "alert": {
            "id": str(alert.id),
            "type": alert.type,
            "severity": alert.severity,
            "message": alert.message,
            "target_type": alert.target_type,
            "target_id": str(alert.target_id),
            "customer_id": str(alert.customer_id) if alert.customer_id else None,
            "opened_at": alert.opened_at.isoformat(),
            "data": alert.data,
        },
    }
    return Message(subject=subject[:500], body=body, payload=payload)


@dataclass
class QueueResult:
    alerts: int = 0
    notifications: int = 0


async def enqueue_new_alerts(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> QueueResult:
    """Every open alert not yet notified becomes one notification per channel destination (rule channels,
    or every active channel of the reseller when the rule has none)."""
    now = now or datetime.now(UTC)
    alerts = list(
        (
            await session.execute(
                select(Alert)
                .where(Alert.notified_at.is_(None), Alert.state != "resolved")
                .order_by(Alert.opened_at)
                .limit(500)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    result = QueueResult()
    by_reseller: dict[uuid.UUID, list[Alert]] = {}
    for a in alerts:
        by_reseller.setdefault(a.reseller_id, []).append(a)
    for reseller_id, items in by_reseller.items():
        await ensure_default_rules(session, reseller_id)  # alerta da ingestão antes da 1ª avaliação
        rules = await load_rules(session, reseller_id)
        cfg = await get_settings(session, reseller_id)
        channels = {
            c.id: c
            for c in (
                await session.execute(
                    select(NotificationChannel).where(
                        NotificationChannel.reseller_id == reseller_id, NotificationChannel.enabled.is_(True)
                    )
                )
            ).scalars()
        }
        for alert in items:
            alert.notified_at = now
            result.alerts += 1
            rule = rules.for_customer(alert.customer_id, alert.type)
            if rule is None or not rule.enabled:
                continue  # regra desligada: o alerta aparece no portal, mas não notifica
            ids = [uuid.UUID(str(i)) for i in rule.channel_ids] or list(channels)
            msg = _alert_message(settings, alert)
            # Silêncio (seção 9): crítico sai na hora; os outros esperam o fim da janela.
            when = now if alert.severity == "critical" else (quiet_until(now, cfg) or now)
            for cid in ids:
                ch = channels.get(cid)
                if ch is None:
                    continue
                for dest in destinations(ch, channel_config(settings, ch)):
                    session.add(
                        Notification(
                            reseller_id=reseller_id,
                            channel_id=ch.id,
                            alert_id=alert.id,
                            kind=ch.kind,
                            destination=dest[:500],
                            subject=msg.subject,
                            body=msg.body,
                            status="pending",
                            next_attempt_at=when,
                        )
                    )
                    result.notifications += 1
    await session.flush()
    return result


@dataclass
class DeliveryResult:
    sent: int = 0
    failed: int = 0
    retried: int = 0
    suppressed: int = 0


async def deliver_due(
    session: AsyncSession, settings: Settings, client: httpx.AsyncClient, now: datetime | None = None
) -> DeliveryResult:
    now = now or datetime.now(UTC)
    rows = list(
        (
            await session.execute(
                select(Notification)
                .where(Notification.status == "pending", Notification.next_attempt_at <= now)
                .order_by(Notification.next_attempt_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    result = DeliveryResult()
    for n in rows:
        alert = await session.get(Alert, n.alert_id) if n.alert_id else None
        if alert is not None and alert.state == "resolved":
            n.status, n.error = "suppressed", "alerta resolvido antes do envio"
            result.suppressed += 1
            continue
        ch = await session.get(NotificationChannel, n.channel_id) if n.channel_id else None
        if ch is None or not ch.enabled:
            n.status, n.error = "suppressed", "canal removido ou desativado"
            result.suppressed += 1
            continue
        n.attempts += 1
        msg = (
            _alert_message(settings, alert)
            if alert is not None
            else Message(n.subject, n.body, {"type": "summary"})
        )
        try:
            await build_notifier(ch.kind, channel_config(settings, ch), settings, client).send(
                n.destination, msg
            )
        except (NotifyError, crypto.DecryptionError) as exc:
            n.error = str(exc)
            if n.attempts >= settings.notification_max_attempts:
                n.status = "failed"
                result.failed += 1
                logger.error("notificação %s para %s falhou de vez: %s", n.id, n.destination, exc)
            else:
                wait = BACKOFF_MINUTES[min(n.attempts - 1, len(BACKOFF_MINUTES) - 1)]
                n.next_attempt_at = now + timedelta(minutes=wait)
                result.retried += 1
                logger.error(
                    "notificação %s para %s falhou (tentativa %d); nova tentativa em %d min: %s",
                    n.id,
                    n.destination,
                    n.attempts,
                    wait,
                    exc,
                )
            continue
        n.status, n.sent_at, n.error = "sent", now, None
        result.sent += 1
    return result


async def retry_notification(session: AsyncSession, p: Principal, notification_id: uuid.UUID) -> Notification:
    p.require("notifications.write")
    n = await session.get(Notification, notification_id)
    if n is None or n.reseller_id != p.reseller_id:
        raise not_found("Notificação")
    if n.status not in ("failed", "suppressed"):
        raise bad_request("not_retryable", "Só notificações com falha ou suprimidas podem ser reenviadas")
    n.status, n.next_attempt_at, n.attempts = "pending", datetime.now(UTC), 0
    await audit.record(
        session,
        p,
        action="retry",
        entity="notification",
        entity_id=n.id,
        reseller_id=n.reseller_id,
        after={"destination": n.destination},
    )
    return n


# ----------------------------------------------------------------------------- resumo diário


async def daily_summary(session: AsyncSession, settings: Settings, now: datetime | None = None) -> int:
    """07:00 (São Paulo): per reseller, e-mail with offline collectors, devices without reading, critical
    toners and open alerts, to the e-mail channels that did not opt out (config daily_summary=false)."""
    now = now or datetime.now(UTC)
    created = 0
    resellers = (await session.execute(select(Reseller).where(Reseller.deleted_at.is_(None)))).scalars().all()
    for reseller in resellers:
        cfg = await get_settings(session, reseller.id)
        if not cfg.daily_summary:
            continue
        channels = [
            c
            for c in (
                await session.execute(
                    select(NotificationChannel).where(
                        NotificationChannel.reseller_id == reseller.id,
                        NotificationChannel.enabled.is_(True),
                        NotificationChannel.kind == "email",
                    )
                )
            ).scalars()
            if channel_config(settings, c).get("daily_summary", True)
        ]
        if not channels:
            continue
        body = await _summary_body(session, settings, reseller, now)
        subject = f"[Dati Monitor] Resumo diário de {now.astimezone(SP):%d/%m/%Y} — {reseller.name}"
        for ch in channels:
            for dest in destinations(ch, {}):
                session.add(
                    Notification(
                        reseller_id=reseller.id,
                        channel_id=ch.id,
                        alert_id=None,
                        kind="email",
                        destination=dest,
                        subject=subject,
                        body=body,
                        status="pending",
                        next_attempt_at=now,
                    )
                )
                created += 1
    await session.flush()
    return created


async def _summary_body(session: AsyncSession, settings: Settings, reseller: Reseller, now: datetime) -> str:
    offline = (
        (
            await session.execute(
                select(Agent.name, Agent.last_seen_at)
                .where(
                    Agent.reseller_id == reseller.id,
                    Agent.deleted_at.is_(None),
                    Agent.revoked_at.is_(None),
                    Agent.enrolled_at.is_not(None),
                    Agent.state == "offline",
                )
                .order_by(Agent.last_seen_at)
            )
        )
        .tuples()
        .all()
    )
    no_reading = (
        await session.execute(
            select(func.count())
            .select_from(Device)
            .where(
                Device.reseller_id == reseller.id,
                Device.deleted_at.is_(None),
                Device.active.is_(True),
                Device.discovery_state == "approved",
                Device.disconnected.is_(True),
            )
        )
    ).scalar_one()
    critical = (
        (
            await session.execute(
                select(
                    Device.serial,
                    Device.model,
                    SupplyCurrent.color,
                    SupplyCurrent.percent,
                    SupplyCurrent.days_to_empty,
                )
                .join(Device, Device.id == SupplyCurrent.device_id)
                .where(
                    SupplyCurrent.reseller_id == reseller.id,
                    SupplyCurrent.supply_class == "consumed",
                    Device.deleted_at.is_(None),
                    Device.discovery_state == "approved",
                    (SupplyCurrent.percent <= CRITICAL_PERCENT)
                    | (SupplyCurrent.days_to_empty <= CRITICAL_DAYS),
                )
                .order_by(SupplyCurrent.percent)
                .limit(50)
            )
        )
        .tuples()
        .all()
    )
    open_alerts = (
        (
            await session.execute(
                select(Alert.severity, func.count())
                .where(Alert.reseller_id == reseller.id, Alert.state != "resolved")
                .group_by(Alert.severity)
            )
        )
        .tuples()
        .all()
    )
    lines = [f"Resumo de {now.astimezone(SP):%d/%m/%Y %H:%M} (horário de Brasília)", ""]
    lines.append(f"Coletores offline: {len(offline)}")
    lines += [
        f"  - {name} (último sinal {seen.astimezone(SP):%d/%m %H:%M})" if seen else f"  - {name}"
        for name, seen in offline
    ]
    lines.append(f"Equipamentos sem leitura (desconectados): {no_reading}")
    lines.append(f"Toners críticos (até 10% ou até 7 dias): {len(critical)}")
    for serial, model, color, pct, days in critical:
        extra = f", ~{days:.0f} dia(s)" if days is not None else ""
        lines.append(
            f"  - {model or ''} {serial}: {color or 'toner'} "
            f"{pct if pct is not None else '?'}%{extra}".strip()
        )
    counts = dict(open_alerts)
    lines.append(
        f"Alertas abertos: {sum(counts.values())} (críticos {counts.get('critical', 0)}, "
        f"atenção {counts.get('warning', 0)}, avisos {counts.get('info', 0)})"
    )
    lines += ["", f"Portal: {settings.public_base_url.rstrip('/')}/alertas"]
    return "\n".join(lines)
