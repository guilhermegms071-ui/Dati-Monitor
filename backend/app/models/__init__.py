"""ORM models. Importing this package registers every table in Base.metadata."""

from app.models.agents import (
    Agent,
    AgentEnrollmentCode,
    AgentHeartbeat,
    AgentLog,
    ClusterEvent,
    IpRange,
    SnmpCredential,
)
from app.models.alerts import Alert, AlertRule, Notification, NotificationChannel
from app.models.base import Base
from app.models.devices import Brand, Device, DeviceEvent, DeviceModel, ReadProfile
from app.models.operations import AgentRelease, AuditLog, Command, ErpToken, MibWalk, Setting
from app.models.readings import (
    Reading,
    ReadingAdjustment,
    ReadingDiscard,
    ReadingIdempotency,
    ReadingReview,
    SupplyCurrent,
    SupplyReading,
)
from app.models.tenancy import Company, Customer, Reseller, Site
from app.models.users import PasswordResetToken, RefreshToken, Role, RolePermission, User

# Tabelas particionadas por mês: (tabela, coluna da partição).
PARTITIONED_TABLES: tuple[tuple[str, str], ...] = (
    ("readings", "read_at"),
    ("supply_readings", "read_at"),
    ("agent_heartbeats", "ts"),
)
# Tabelas somente-inserção (trigger bloqueia UPDATE/DELETE/TRUNCATE).
APPEND_ONLY_TABLES: tuple[str, ...] = ("readings", "audit_log")

__all__ = [
    "APPEND_ONLY_TABLES",
    "PARTITIONED_TABLES",
    "Agent",
    "AgentEnrollmentCode",
    "AgentHeartbeat",
    "AgentLog",
    "AgentRelease",
    "Alert",
    "AlertRule",
    "AuditLog",
    "Base",
    "Brand",
    "ClusterEvent",
    "Command",
    "Company",
    "Customer",
    "Device",
    "DeviceEvent",
    "DeviceModel",
    "ErpToken",
    "IpRange",
    "MibWalk",
    "Notification",
    "NotificationChannel",
    "PasswordResetToken",
    "ReadProfile",
    "Reading",
    "ReadingAdjustment",
    "ReadingDiscard",
    "ReadingIdempotency",
    "ReadingReview",
    "RefreshToken",
    "Reseller",
    "Role",
    "RolePermission",
    "Setting",
    "Site",
    "SnmpCredential",
    "SupplyCurrent",
    "SupplyReading",
    "User",
]
