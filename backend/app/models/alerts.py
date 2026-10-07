"""Alerts, alert rules, notification channels and the notification delivery log."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, LargeBinary, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB_EMPTY_ARRAY, JSONB_EMPTY_OBJECT, Base, IdMixin, TimestampMixin, one_of

ALERT_SEVERITIES = ("info", "warning", "critical")
ALERT_STATES = ("open", "acknowledged", "resolved")
ALERT_TARGETS = ("device", "agent", "site")
ALERT_RULE_TYPES = (
    "toner_low",
    "toner_days_left",
    "device_no_reading",
    "agent_offline",
    "counter_regression",
    "suspicious_jump",
    "sum_mismatch",
    "hardware_error",
    "paper_jam",
    "door_open",
    "jam_recurrent",  # N atolamentos em X dias (seção 16.4)
    "printer_alert",  # alerta da prtAlertTable por categoria (seção 16.4)
    "agent_uninstalled",  # o desinstalador avisou que o coletor saiu do PC
)
CHANNEL_KINDS = ("email", "whatsapp", "webhook")
NOTIFICATION_STATES = ("pending", "sent", "failed", "suppressed")


class AlertRule(Base, IdMixin, TimestampMixin):
    __tablename__ = "alert_rules"
    __table_args__ = (one_of("type", ALERT_RULE_TYPES), one_of("severity", ALERT_SEVERITIES))

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    severity: Mapped[str] = mapped_column(String(16), server_default=text("'warning'"), default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    channel_ids: Mapped[list[Any]] = mapped_column(server_default=JSONB_EMPTY_ARRAY, default=list)


class Alert(Base, IdMixin, TimestampMixin):
    __tablename__ = "alerts"
    __table_args__ = (
        one_of("severity", ALERT_SEVERITIES),
        one_of("state", ALERT_STATES),
        one_of("target_type", ALERT_TARGETS),
        Index("ix_alerts_state_type", "state", "type"),
        # Deduplicação: no máximo um alerta não resolvido por chave.
        Index("uq_alerts_dedup_open", "dedup_key", unique=True, postgresql_where=text("state <> 'resolved'")),
        Index("ix_alerts_pending_notification", "opened_at", postgresql_where=text("notified_at IS NULL")),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"))
    rule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("alert_rules.id"))
    type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[uuid.UUID]
    state: Mapped[str] = mapped_column(String(16), server_default=text("'open'"), default="open")
    message: Mapped[str] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(String(300))
    data: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    opened_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(default=None)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Fila de notificação: o worker enfileira os envios de todo alerta aberto ainda não notificado.
    notified_at: Mapped[datetime | None] = mapped_column(default=None)


class NotificationChannel(Base, IdMixin, TimestampMixin):
    __tablename__ = "notification_channels"
    __table_args__ = (one_of("kind", CHANNEL_KINDS),)

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    config_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # credenciais cifradas (AES-GCM)
    recipients: Mapped[list[Any]] = mapped_column(server_default=JSONB_EMPTY_ARRAY, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)


class Notification(Base, IdMixin, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        one_of("status", NOTIFICATION_STATES),
        Index("ix_notifications_status_next_attempt_at", "status", "next_attempt_at"),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    channel_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("notification_channels.id"))
    alert_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("alerts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    destination: Mapped[str] = mapped_column(String(500))
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
