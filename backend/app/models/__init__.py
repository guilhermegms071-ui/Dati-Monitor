"""ORM models. Importing this package registers every table in Base.metadata."""

from app.models.agents import (
    Agent,
    AgentEnrollmentCode,
    AgentHeartbeat,
    AgentLog,
    AgentPresence,
    ClusterEvent,
    IpRange,
    SnmpCredential,
)
from app.models.alerts import Alert, AlertRule, Notification, NotificationChannel
from app.models.base import Base
from app.models.devices import (
    Brand,
    CustomFieldDefinition,
    Device,
    DeviceAssignment,
    DeviceAttributeSnapshot,
    DeviceEvent,
    DeviceModel,
    PrinterAlert,
    ReadProfile,
)
from app.models.operations import (
    AgentRelease,
    AuditLog,
    Command,
    ErpQueueItem,
    ErpToken,
    Installer,
    MibWalk,
    Setting,
    WebSession,
)
from app.models.readings import (
    Reading,
    ReadingAdjustment,
    ReadingCounter,
    ReadingDiscard,
    ReadingIdempotency,
    ReadingReview,
    SupplyCurrent,
    SupplyReading,
    SupplyReplacement,
)
from app.models.tenancy import Company, Customer, Reseller, Site
from app.models.users import (
    PasswordResetToken,
    RefreshToken,
    ResellerRolePermission,
    Role,
    RolePermission,
    User,
)

# Tabelas particionadas por mês: (tabela, coluna da partição).
PARTITIONED_TABLES: tuple[tuple[str, str], ...] = (
    ("readings", "read_at"),
    ("reading_counters", "read_at"),
    ("supply_readings", "read_at"),
    ("agent_heartbeats", "ts"),
)
# Tabelas somente-inserção (trigger bloqueia UPDATE/DELETE/TRUNCATE).
APPEND_ONLY_TABLES: tuple[str, ...] = ("readings", "reading_counters", "audit_log")

__all__ = [
    "APPEND_ONLY_TABLES",
    "PARTITIONED_TABLES",
    "Agent",
    "AgentEnrollmentCode",
    "AgentHeartbeat",
    "AgentLog",
    "AgentPresence",
    "AgentRelease",
    "Alert",
    "AlertRule",
    "AuditLog",
    "Base",
    "Brand",
    "ClusterEvent",
    "Command",
    "Company",
    "CustomFieldDefinition",
    "Customer",
    "Device",
    "DeviceAssignment",
    "DeviceAttributeSnapshot",
    "DeviceEvent",
    "DeviceModel",
    "ErpQueueItem",
    "ErpToken",
    "Installer",
    "IpRange",
    "MibWalk",
    "Notification",
    "NotificationChannel",
    "PasswordResetToken",
    "PrinterAlert",
    "ReadProfile",
    "Reading",
    "ReadingAdjustment",
    "ReadingCounter",
    "ReadingDiscard",
    "ReadingIdempotency",
    "ReadingReview",
    "RefreshToken",
    "Reseller",
    "ResellerRolePermission",
    "Role",
    "RolePermission",
    "Setting",
    "Site",
    "SnmpCredential",
    "SupplyCurrent",
    "SupplyReading",
    "SupplyReplacement",
    "User",
    "WebSession",
]
