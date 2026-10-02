"""Printers (equipamentos), brands/models, read profiles and the device event timeline."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    JSONB_EMPTY_ARRAY,
    JSONB_EMPTY_OBJECT,
    Base,
    ClockCreatedMixin,
    CreatedMixin,
    IdMixin,
    SoftDeleteMixin,
    TimestampMixin,
    one_of,
)

DEVICE_STATUSES = ("ready", "printing", "warmup", "energy_saving", "warning", "error", "offline", "unknown")
DEVICE_SOURCES = ("snmp", "http", "usb", "manual")
DISCOVERY_STATES = ("pending", "approved", "discarded")
TONER_MODES = ("off", "global", "individual")
CUSTOM_FIELD_TYPES = ("text", "number", "date")
PRINTER_ALERT_CATEGORIES = ("parts", "service_call", "jam", "consumable", "other")
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
    "approved",
    "discarded",
    "restored",
    "supply_replaced",
)


class Brand(Base, IdMixin, TimestampMixin):
    __tablename__ = "brands"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    enterprise_id: Mapped[int | None] = mapped_column(Integer)
    sys_object_id_prefix: Mapped[str | None] = mapped_column(String(128))


class ReadProfile(Base, IdMixin, TimestampMixin):
    __tablename__ = "read_profiles"
    __table_args__ = (UniqueConstraint("profile_key", "version"), one_of("source", ("file", "portal")))

    profile_key: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    content_yaml: Mapped[str] = mapped_column(Text)
    content: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # file = sincronizado de /profiles; portal = publicado na tela Perfis de modelos (sobrevive ao reinício).
    source: Mapped[str] = mapped_column(String(16), server_default=text("'file'"), default="file")
    notes: Mapped[str | None] = mapped_column(Text)


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
        one_of("discovery_state", DISCOVERY_STATES),
        one_of("toner_mode", TONER_MODES),
        Index("ix_devices_site_id_ip", "site_id", "ip"),
        Index("ix_devices_reseller_id_discovery_state", "reseller_id", "discovery_state"),
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
    # Descobertas (seção 16.1): novo entra pendente; só aprovado aparece no parque/relatórios/alertas.
    discovery_state: Mapped[str] = mapped_column(
        String(16), server_default=text("'pending'"), default="pending"
    )
    discovery_decided_at: Mapped[datetime | None] = mapped_column(default=None)
    discovery_decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Cadastro (seção 16.7).
    alt_serial: Mapped[str | None] = mapped_column(String(128))
    sys_location: Mapped[str | None] = mapped_column(String(255))
    # Setor segue o sysLocation até alguém editá-lo no portal.
    sector_from_snmp: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    franchise_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    franchise_pages_mono: Mapped[int | None] = mapped_column(Integer)
    franchise_pages_color: Mapped[int | None] = mapped_column(Integer)
    overage_price_mono: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    overage_price_color: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    custom_fields: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    # Limiar de toner (seção 16.5): off | global (herda do cliente) | individual.
    toner_mode: Mapped[str] = mapped_column(String(16), server_default=text("'global'"), default="global")
    toner_thresholds: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    # Atributos da leitura diária (seção 16.8); o histórico fica em device_attribute_snapshots.
    attributes: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    attributes_at: Mapped[datetime | None] = mapped_column(default=None)


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


class DeviceAttributeSnapshot(Base, IdMixin, CreatedMixin):
    """Atributos da leitura diária (seção 16.8). Só grava quando mudam (uptime não conta)."""

    __tablename__ = "device_attribute_snapshots"
    __table_args__ = (
        Index("ix_device_attribute_snapshots_device_id_read_at", "device_id", text("read_at DESC")),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    read_at: Mapped[datetime]
    attributes: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))


class CustomFieldDefinition(Base, IdMixin, TimestampMixin):
    """Campo personalizado de equipamento definido pela revenda (seção 16.7)."""

    __tablename__ = "custom_field_definitions"
    __table_args__ = (UniqueConstraint("reseller_id", "key"), one_of("field_type", CUSTOM_FIELD_TYPES))

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(100))
    field_type: Mapped[str] = mapped_column(String(16), server_default=text("'text'"), default="text")
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)


class PrinterAlert(Base, IdMixin, TimestampMixin):
    """Alerta da própria impressora (prtAlertTable, RFC 3805; seção 16.4).

    Uma linha por alerta novo; `cleared_at` quando ele some da tabela. A identidade (`alert_key`) combina
    índice, código, grupo, local e o prtAlertTime, que muda a cada novo alerta mesmo no mesmo índice.
    """

    __tablename__ = "printer_alerts"
    __table_args__ = (
        one_of("category", PRINTER_ALERT_CATEGORIES),
        Index(
            "uq_printer_alerts_device_id_alert_key_active",
            "device_id",
            "alert_key",
            unique=True,
            postgresql_where=text("cleared_at IS NULL"),
        ),
        Index(
            "ix_printer_alerts_reseller_id_category_first_seen_at", "reseller_id", "category", "first_seen_at"
        ),
        Index("ix_printer_alerts_device_id_first_seen_at", "device_id", "first_seen_at"),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    alert_key: Mapped[str] = mapped_column(String(64))
    prt_index: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[int] = mapped_column(Integer)
    training_level: Mapped[int | None] = mapped_column(Integer)
    group: Mapped[int | None] = mapped_column(Integer)
    group_index: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[int | None] = mapped_column(Integer)
    code: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    alert_time_ticks: Mapped[int | None] = mapped_column(BigInteger)
    category: Mapped[str] = mapped_column(String(16))
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    cleared_at: Mapped[datetime | None] = mapped_column(default=None)
    # Contadores do equipamento no momento em que o alerta apareceu.
    total_at: Mapped[int | None] = mapped_column(BigInteger)
    mono_at: Mapped[int | None] = mapped_column(BigInteger)
    color_at: Mapped[int | None] = mapped_column(BigInteger)
