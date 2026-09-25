"""Printers (equipamentos), brands/models, read profiles and the device event timeline."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    JSONB_EMPTY_ARRAY,
    JSONB_EMPTY_OBJECT,
    Base,
    ClockCreatedMixin,
    IdMixin,
    SoftDeleteMixin,
    TimestampMixin,
    one_of,
)

DEVICE_STATUSES = ("ready", "printing", "warmup", "energy_saving", "warning", "error", "offline", "unknown")
DEVICE_SOURCES = ("snmp", "http", "usb", "manual")
DEVICE_EVENT_TYPES = (
    "discovered",
    "ip_changed",
    "moved_site",
    "replaced",
    "counter_regression",
    "reactivated",
    "deactivated",
    "manual_adjust",
    "read_failed",
    "suspicious_jump",
    "sum_mismatch",
    "reading_discarded",
    "reading_classified",
)


class Brand(Base, IdMixin, TimestampMixin):
    __tablename__ = "brands"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    enterprise_id: Mapped[int | None] = mapped_column(Integer)
    sys_object_id_prefix: Mapped[str | None] = mapped_column(String(128))


class ReadProfile(Base, IdMixin, TimestampMixin):
    __tablename__ = "read_profiles"
    __table_args__ = (UniqueConstraint("profile_key", "version"),)

    profile_key: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    content_yaml: Mapped[str] = mapped_column(Text)
    content: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class DeviceModel(Base, IdMixin, TimestampMixin):
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("brand_id", "name"), one_of("max_paper", ("A4", "A3")))

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id"))
    name: Mapped[str] = mapped_column(String(200))
    sys_object_id_prefix: Mapped[str | None] = mapped_column(String(128))
    is_color: Mapped[bool | None] = mapped_column(Boolean)
    max_paper: Mapped[str | None] = mapped_column(String(4))
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("read_profiles.id"))


class Device(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("reseller_id", "serial"),
        one_of("last_status", DEVICE_STATUSES),
        one_of("source", DEVICE_SOURCES),
        Index("ix_devices_site_id_ip", "site_id", "ip"),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    serial: Mapped[str] = mapped_column(String(128))
    mac: Mapped[str | None] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(String(64))
    snmp_port: Mapped[int] = mapped_column(Integer, server_default=text("161"), default=161)
    hostname: Mapped[str | None] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    brand_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("brands.id"))
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("models.id"))
    sys_object_id: Mapped[str | None] = mapped_column(String(128))
    sys_descr: Mapped[str | None] = mapped_column(Text)
    firmware: Mapped[str | None] = mapped_column(String(200))
    asset_tag: Mapped[str | None] = mapped_column(String(100))  # PAT
    sector: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    is_color: Mapped[bool | None] = mapped_column(Boolean)
    profile_key: Mapped[str | None] = mapped_column(String(100))
    counter_source: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), server_default=text("'snmp'"), default="snmp")
    usb_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    first_seen_at: Mapped[datetime] = mapped_column(server_default=text("now()"))  # "Descoberta"
    last_read_at: Mapped[datetime | None] = mapped_column(default=None)  # "Comunicação"
    last_status: Mapped[str] = mapped_column(String(16), server_default=text("'unknown'"), default="unknown")
    last_status_at: Mapped[datetime | None] = mapped_column(default=None)
    last_error_bits: Mapped[int | None] = mapped_column(Integer)
    last_error_reasons: Mapped[list[Any]] = mapped_column(server_default=JSONB_EMPTY_ARRAY, default=list)
    last_panel_text: Mapped[str | None] = mapped_column(Text)
    # Últimos contadores (cópia de conveniência para a tela de parque; a fonte é `readings`).
    last_total: Mapped[int | None] = mapped_column(BigInteger)
    last_mono: Mapped[int | None] = mapped_column(BigInteger)
    last_color: Mapped[int | None] = mapped_column(BigInteger)
    disconnected: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    monitored: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    last_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))  # "DCA"


class DeviceEvent(Base, IdMixin, ClockCreatedMixin):
    """Linha do tempo do equipamento (somente inserção)."""

    __tablename__ = "device_events"
    __table_args__ = (
        one_of("type", DEVICE_EVENT_TYPES),
        Index("ix_device_events_device_id_created_at", "device_id", text("created_at DESC")),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"))
    type: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
