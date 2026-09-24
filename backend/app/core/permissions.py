"""Roles and per-action permissions (PROMPT section 3: roles / role_permissions).

The matrix below is the system definition; `sync_roles` (services.bootstrap) mirrors it into the
`roles` and `role_permissions` tables at startup so the database always reflects this code.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class RoleDef:
    code: str
    name: str
    level: int


SUPERADMIN: Final = "superadmin"
RESELLER_ADMIN: Final = "reseller_admin"
OPERATOR: Final = "operator"
TECHNICIAN: Final = "technician"
CUSTOMER_VIEWER: Final = "customer_viewer"

ROLES: Final[tuple[RoleDef, ...]] = (
    RoleDef(SUPERADMIN, "Superadministrador", 100),
    RoleDef(RESELLER_ADMIN, "Administrador da revenda", 80),
    RoleDef(OPERATOR, "Operador", 60),
    RoleDef(TECHNICIAN, "Técnico", 40),
    RoleDef(CUSTOMER_VIEWER, "Cliente (somente leitura)", 10),
)
ROLE_BY_CODE: Final = {r.code: r for r in ROLES}

ALL_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        "resellers.read",
        "resellers.write",
        "companies.read",
        "companies.write",
        "customers.read",
        "customers.write",
        "sites.read",
        "sites.write",
        "users.read",
        "users.write",
        "audit.read",
        "agents.read",
        "agents.write",
        "agents.command",
        "agents.admin",
        "devices.read",
        "devices.write",
        "devices.web_access",
        "readings.adjust",
        "alerts.read",
        "alerts.manage",
        "alert_rules.write",
        "notifications.write",
        "reports.read",
        "profiles.read",
        "profiles.write",
        "releases.read",
        "releases.publish",
        "settings.read",
        "settings.write",
        "erp_tokens.write",
    }
)

_READ_OPERATIONAL = {
    "companies.read",
    "customers.read",
    "sites.read",
    "agents.read",
    "devices.read",
    "alerts.read",
    "reports.read",
    "profiles.read",
    "releases.read",
}

ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    SUPERADMIN: ALL_PERMISSIONS,
    RESELLER_ADMIN: ALL_PERMISSIONS - {"resellers.write"},
    OPERATOR: frozenset(
        _READ_OPERATIONAL
        | {
            "companies.write",
            "customers.write",
            "sites.write",
            "agents.write",
            "agents.command",
            "devices.write",
            "readings.adjust",
            "alerts.manage",
            "settings.read",
        }
    ),
    TECHNICIAN: frozenset(
        _READ_OPERATIONAL
        | {"agents.command", "devices.write", "devices.web_access", "alerts.manage", "profiles.write"}
    ),
    CUSTOMER_VIEWER: frozenset(
        {"customers.read", "sites.read", "agents.read", "devices.read", "alerts.read", "reports.read"}
    ),
}


def role_level(code: str) -> int:
    return ROLE_BY_CODE[code].level
