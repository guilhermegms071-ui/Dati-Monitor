"""Collectors (dm-agent), enrollment, heartbeats, discovery ranges, SNMP credentials and cluster history."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
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
    CreatedMixin,
    IdMixin,
    SoftDeleteMixin,
    TimestampMixin,
    one_of,
)

AGENT_KINDS = ("windows", "linux")
UPDATE_CHANNELS = ("canary", "stable")
CLUSTER_ROLES = ("master", "standby")
AGENT_STATES = ("online", "offline", "degraded", "paused")


class Agent(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "agents"
    __table_args__ = (
        one_of("kind", AGENT_KINDS),
        one_of("update_channel", UPDATE_CHANNELS),
        one_of("cluster_role", CLUSTER_ROLES),
        one_of("state", AGENT_STATES),
        Index("ix_agents_site_id_cluster_role", "site_id", "cluster_role"),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), server_default=text("'windows'"), default="windows")
    hostname: Mapped[str | None] = mapped_column(String(255))
    os: Mapped[str | None] = mapped_column(String(255))
    arch: Mapped[str | None] = mapped_column(String(32))
    local_ips: Mapped[list[Any]] = mapped_column(server_default=JSONB_EMPTY_ARRAY, default=list)
    host_mac: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[str | None] = mapped_column(String(64))
    watchdog_version: Mapped[str | None] = mapped_column(String(64))
    update_channel: Mapped[str] = mapped_column(String(16), server_default=text("'stable'"), default="stable")
    cluster_role: Mapped[str] = mapped_column(String(16), server_default=text("'standby'"), default="standby")
    priority: Mapped[int] = mapped_column(Integer, server_default=text("100"), default=100)
    state: Mapped[str] = mapped_column(String(16), server_default=text("'offline'"), default="offline")
    last_seen_at: Mapped[datetime | None] = mapped_column(default=None)
    last_watchdog_seen_at: Mapped[datetime | None] = mapped_column(default=None)
    secret_hash: Mapped[str | None] = mapped_column(String(128))
    enrolled_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    config: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    config_version: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    applied_config_version: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    queue_pending: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    avg_latency_ms: Mapped[float | None] = mapped_column(Float)
    last_error: Mapped[str | None] = mapped_column(Text)
    # Sub-redes sugeridas pelo agente quando o Local não tem faixa aprovada (seção 4.5).
    suggested_ranges: Mapped[list[Any]] = mapped_column(server_default=JSONB_EMPTY_ARRAY, default=list)


class AgentEnrollmentCode(Base, IdMixin, TimestampMixin):
    __tablename__ = "agent_enrollment_codes"

    code: Mapped[str] = mapped_column(String(8), unique=True)
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AgentHeartbeat(Base, CreatedMixin):
    """Particionada por mês em `ts`; retida por 30 dias (job de retenção)."""

    __tablename__ = "agent_heartbeats"
    __table_args__ = (
        PrimaryKeyConstraint("id", "ts"),
        one_of("channel", ("ws", "http", "watchdog")),
        Index("ix_agent_heartbeats_agent_id_ts", "agent_id", text("ts DESC")),
        {"postgresql_partition_by": "RANGE (ts)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    ts: Mapped[datetime]
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"))
    channel: Mapped[str] = mapped_column(String(16))
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    queue_pending: Mapped[int | None] = mapped_column(Integer)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[str | None] = mapped_column(String(64))
    cluster_role: Mapped[str | None] = mapped_column(String(16))
    latency_ms: Mapped[float | None] = mapped_column(Float)


class IpRange(Base, IdMixin, TimestampMixin):
    __tablename__ = "ip_ranges"
    __table_args__ = (one_of("status", ("approved", "suggested")),)

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    cidr: Mapped[str | None] = mapped_column(String(64))
    start_ip: Mapped[str | None] = mapped_column(String(64))
    end_ip: Mapped[str | None] = mapped_column(String(64))
    exclusions: Mapped[list[Any]] = mapped_column(server_default=JSONB_EMPTY_ARRAY, default=list)
    active: Mapped[bool] = mapped_column(server_default=text("true"), default=True)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'approved'"), default="approved")
    suggested_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))


class SnmpCredential(Base, IdMixin, TimestampMixin):
    __tablename__ = "snmp_credentials"
    __table_args__ = (
        UniqueConstraint("site_id", "position"),
        one_of("version", ("v1", "v2c", "v3")),
        one_of("v3_auth_protocol", ("SHA", "SHA256"), name="v3_auth_protocol_valid"),
        one_of("v3_priv_protocol", ("AES", "AES256"), name="v3_priv_protocol_valid"),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    position: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(8))
    community_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    v3_username: Mapped[str | None] = mapped_column(String(128))
    v3_auth_protocol: Mapped[str | None] = mapped_column(String(16))
    v3_auth_password_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    v3_priv_protocol: Mapped[str | None] = mapped_column(String(16))
    v3_priv_password_enc: Mapped[bytes | None] = mapped_column(LargeBinary)


class ClusterEvent(Base, IdMixin, CreatedMixin):
    """Histórico de trocas de MASTER por Local (seção 4.8)."""

    __tablename__ = "cluster_events"

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    from_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    to_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    reason: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AgentLog(Base, IdMixin, CreatedMixin):
    __tablename__ = "agent_logs"
    __table_args__ = (one_of("source", ("agent", "watchdog")),)

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)
    command_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commands.id"))
    source: Mapped[str] = mapped_column(String(16))
    file_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    hours: Mapped[int | None] = mapped_column(Integer)
