"""Readings (immutable, partitioned), idempotency, reviews, adjustments and supplies."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    JSONB_EMPTY_ARRAY,
    JSONB_EMPTY_OBJECT,
    Base,
    CreatedMixin,
    IdMixin,
    TimestampMixin,
    one_of,
)

READING_SOURCES = ("snmp", "http", "usb", "manual")
COUNTER_FIELDS = (
    "total",
    "mono",
    "color",
    "mono_large",
    "color_large",
    "copy_mono",
    "copy_color",
    "print_mono",
    "print_color",
    "scan",
    "fax",
)


class Reading(Base):
    """Leitura de contadores. Somente INSERT (trigger bloqueia UPDATE/DELETE/TRUNCATE).

    `flags` é definido na ingestão (counter_regression, suspicious_jump, sum_mismatch) e nunca muda;
    a classificação posterior do operador fica em `reading_reviews`.
    """

    __tablename__ = "readings"
    __table_args__ = (
        PrimaryKeyConstraint("id", "read_at"),
        one_of("source", READING_SOURCES),
        Index("ix_readings_device_id_read_at", "device_id", text("read_at DESC")),
        Index("ix_readings_reseller_id_read_at", "reseller_id", "read_at"),
        {"postgresql_partition_by": "RANGE (read_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    read_at: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # leitura manual
    idempotency_key: Mapped[str] = mapped_column(String(200))
    total: Mapped[int | None] = mapped_column(BigInteger)
    mono: Mapped[int | None] = mapped_column(BigInteger)
    color: Mapped[int | None] = mapped_column(BigInteger)
    mono_large: Mapped[int | None] = mapped_column(BigInteger)
    color_large: Mapped[int | None] = mapped_column(BigInteger)
    copy_mono: Mapped[int | None] = mapped_column(BigInteger)
    copy_color: Mapped[int | None] = mapped_column(BigInteger)
    print_mono: Mapped[int | None] = mapped_column(BigInteger)
    print_color: Mapped[int | None] = mapped_column(BigInteger)
    scan: Mapped[int | None] = mapped_column(BigInteger)
    fax: Mapped[int | None] = mapped_column(BigInteger)
    extra: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    status: Mapped[str | None] = mapped_column(String(16))
    error_bits: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), server_default=text("'snmp'"), default="snmp")
    profile_key: Mapped[str | None] = mapped_column(String(100))
    counter_source: Mapped[str | None] = mapped_column(String(64))
    flags: Mapped[list[Any]] = mapped_column(server_default=JSONB_EMPTY_ARRAY, default=list)


class ReadingIdempotency(Base, CreatedMixin):
    """Unicidade global de `idempotency_key`.

    Tabelas particionadas não aceitam UNIQUE sem a chave de partição.
    """

    __tablename__ = "reading_idempotency"

    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    reading_id: Mapped[uuid.UUID]
    read_at: Mapped[datetime]


class ReadingReview(Base, IdMixin, TimestampMixin):
    """Classificação do operador para leituras sinalizadas (ex.: regressão de contador)."""

    __tablename__ = "reading_reviews"
    __table_args__ = (
        ForeignKeyConstraint(["reading_id", "read_at"], ["readings.id", "readings.read_at"]),
        one_of("classification", ("board_replacement", "read_error", "valid")),
        Index("uq_reading_reviews_reading", "reading_id", unique=True),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    reading_id: Mapped[uuid.UUID]
    read_at: Mapped[datetime]
    classification: Mapped[str] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class ReadingAdjustment(Base, IdMixin, CreatedMixin):
    """Correção de leitura: nunca edita `readings`; registra os valores corrigidos, autor e motivo."""

    __tablename__ = "reading_adjustments"
    __table_args__ = (
        ForeignKeyConstraint(["reading_id", "read_at"], ["readings.id", "readings.read_at"]),
        Index("ix_reading_adjustments_device_id_created_at", "device_id", "created_at"),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"))
    reading_id: Mapped[uuid.UUID]
    read_at: Mapped[datetime]
    total: Mapped[int | None] = mapped_column(BigInteger)
    mono: Mapped[int | None] = mapped_column(BigInteger)
    color: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class ReadingDiscard(Base, IdMixin, CreatedMixin):
    """Leituras recebidas e descartadas (anti-duplicidade do cluster, seção 4.8), com o motivo."""

    __tablename__ = "reading_discards"

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    read_at: Mapped[datetime]
    idempotency_key: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)


SUPPLY_LEVEL_STATES = ("ok", "unknown", "some_remaining")
SUPPLY_CLASSES = ("consumed", "receptacle", "other")


class SupplyReading(Base, CreatedMixin):
    __tablename__ = "supply_readings"
    __table_args__ = (
        PrimaryKeyConstraint("id", "read_at"),
        one_of("level_state", SUPPLY_LEVEL_STATES),
        one_of("supply_class", SUPPLY_CLASSES),
        Index("ix_supply_readings_device_id_supply_key_read_at", "device_id", "supply_key", "read_at"),
        {"postgresql_partition_by": "RANGE (read_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    read_at: Mapped[datetime]
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"))
    supply_key: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(255))
    supply_type: Mapped[str | None] = mapped_column(String(32))
    supply_class: Mapped[str] = mapped_column(
        String(16), server_default=text("'consumed'"), default="consumed"
    )
    color: Mapped[str | None] = mapped_column(String(32))
    level: Mapped[int | None] = mapped_column(Integer)
    max_capacity: Mapped[int | None] = mapped_column(Integer)
    percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    level_state: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(32))


class SupplyCurrent(Base, TimestampMixin):
    """Último estado por equipamento + suprimento (tela de parque rápida) e previsão de término."""

    __tablename__ = "supplies_current"
    __table_args__ = (
        PrimaryKeyConstraint("device_id", "supply_key"),
        one_of("level_state", SUPPLY_LEVEL_STATES),
        one_of("supply_class", SUPPLY_CLASSES),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    supply_key: Mapped[str] = mapped_column(String(100))
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    read_at: Mapped[datetime]
    description: Mapped[str | None] = mapped_column(String(255))
    supply_type: Mapped[str | None] = mapped_column(String(32))
    supply_class: Mapped[str] = mapped_column(
        String(16), server_default=text("'consumed'"), default="consumed"
    )
    color: Mapped[str | None] = mapped_column(String(32))
    level: Mapped[int | None] = mapped_column(Integer)
    max_capacity: Mapped[int | None] = mapped_column(Integer)
    percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    level_state: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(32))
    days_to_empty: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    forecast_at: Mapped[datetime | None] = mapped_column(default=None)
