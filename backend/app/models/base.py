"""Declarative base, common columns and helpers shared by every model."""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy API
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        datetime: DateTime(timezone=True),
        uuid.UUID: UUID(as_uuid=True),
    }
    # Valores gerados pelo servidor (now(), gen_random_uuid()) voltam no próprio INSERT/UPDATE via
    # RETURNING: nada fica "expirado" para ser carregado depois (em async isso seria I/O implícito).
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 - SQLAlchemy API


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class CreatedMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ClockCreatedMixin:
    """created_at com clock_timestamp(): ordem estável mesmo entre linhas da mesma transação."""

    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())


class TimestampMixin(CreatedMixin):
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


def one_of(column: str, values: Iterable[str], name: str | None = None) -> CheckConstraint:
    """CHECK constraint restricting a text column to a fixed set (portable alternative to PG enums)."""
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name or f"{column}_valid")


JSONB_EMPTY_OBJECT = text("'{}'::jsonb")
JSONB_EMPTY_ARRAY = text("'[]'::jsonb")
