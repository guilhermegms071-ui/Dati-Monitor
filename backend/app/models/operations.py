"""Remote commands, agent releases, MIB walks, audit log, settings and ERP integration tokens."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    JSONB_EMPTY_OBJECT,
    Base,
    ClockCreatedMixin,
    CreatedMixin,
    IdMixin,
    TimestampMixin,
    one_of,
)

COMMAND_STATES = ("pending", "sent", "acked", "running", "succeeded", "failed", "expired", "cancelled")
COMMAND_TARGETS = ("agent", "watchdog")


class Command(Base, IdMixin, TimestampMixin):
    __tablename__ = "commands"
    __table_args__ = (
        one_of("state", COMMAND_STATES),
        one_of("target", COMMAND_TARGETS),
        Index("ix_commands_agent_id_state", "agent_id", "state"),
        Index("ix_commands_state_expires_at", "state", "expires_at"),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    target: Mapped[str] = mapped_column(String(16), server_default=text("'agent'"), default="agent")
    type: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    state: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"), default="pending")
    result: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    output: Mapped[str | None] = mapped_column(Text)  # limitado a 1 MB na aplicação
    progress: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commands.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
    acked_at: Mapped[datetime | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime]


class AgentRelease(Base, IdMixin, TimestampMixin):
    __tablename__ = "agent_releases"
    __table_args__ = (
        UniqueConstraint("component", "version", "os", "arch"),
        one_of("component", ("agent", "watchdog")),
        one_of("channel", ("canary", "stable")),
    )

    component: Mapped[str] = mapped_column(String(16), server_default=text("'agent'"), default="agent")
    version: Mapped[str] = mapped_column(String(64))
    os: Mapped[str] = mapped_column(String(16))
    arch: Mapped[str] = mapped_column(String(16))
    file_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(Text)  # ed25519, base64
    channel: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)
    rollout_percent: Mapped[int] = mapped_column(Integer, server_default=text("100"), default=100)
    published_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    yanked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)


class MibWalk(Base, IdMixin, CreatedMixin):
    __tablename__ = "mib_walks"

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    command_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commands.id"))
    ip: Mapped[str] = mapped_column(String(64))
    root_oid: Mapped[str | None] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(Text)
    oid_count: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AuditLog(Base, IdMixin, ClockCreatedMixin):
    """Toda ação de escrita do portal. Somente inserção (trigger)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_reseller_id_created_at", "reseller_id", text("created_at DESC")),
        Index("ix_audit_log_entity_entity_id", "entity", "entity_id"),
    )

    reseller_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resellers.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    after: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    ip: Mapped[str | None] = mapped_column(String(64))


class Setting(Base, IdMixin, TimestampMixin):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("reseller_id", "key"),)

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"))
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    value_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # valores sensíveis (AES-GCM)


class ErpToken(Base, IdMixin, TimestampMixin):
    """Token de integração somente leitura para o ERP (seção 7)."""

    __tablename__ = "erp_tokens"

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    token_prefix: Mapped[str] = mapped_column(String(12))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
