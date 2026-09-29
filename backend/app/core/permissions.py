"""Roles and per-action permissions (PROMPT section 3: roles / role_permissions; matrix of section 16.14).

Permissions are a matrix of modules (Clientes, Coletores, Equipamentos, Relatórios, Integração, Usuários)
x actions (Consultar, Incluir, Alterar, Excluir), plus "Monitorar suprimentos" and a few special
permissions. The defaults below are the system definition; `sync_roles` (services.bootstrap) mirrors them
into `roles`/`role_permissions` at startup. A reseller may adjust the matrix of the operational roles
(`reseller_role_permissions`, services.permissions); special permissions always follow the role.
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

# Matriz (seção 16.14). Clientes inclui empresas e locais; Coletores inclui faixas de IP e credenciais SNMP.
MODULES: Final[dict[str, str]] = {
    "customers": "Clientes",
    "agents": "Coletores",
    "devices": "Equipamentos",
    "reports": "Relatórios",
    "integration": "Integração",
    "users": "Usuários",
}
ACTIONS: Final[dict[str, str]] = {
    "read": "Consultar",
    "create": "Incluir",
    "update": "Alterar",
    "delete": "Excluir",
}
SUPPLIES_MONITOR: Final = "supplies.monitor"
MATRIX_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {f"{m}.{a}" for m in MODULES for a in ACTIONS} | {SUPPLIES_MONITOR}
)

# Permissões especiais: seguem sempre o papel (não entram na matriz ajustável).
SPECIAL_PERMISSIONS: Final[dict[str, str]] = {
    "resellers.read": "Ver revendas",
    "resellers.write": "Gerenciar revendas",
    "audit.read": "Ver auditoria",
    "agents.command": "Enviar comandos aos coletores",
    "agents.admin": "Administrar coletores (desinstalar, cluster)",
    "devices.web_access": "Abrir a página web da impressora",
    "readings.adjust": "Ajustar leituras",
    "alerts.read": "Ver alertas",
    "alerts.manage": "Reconhecer e resolver alertas",
    "alert_rules.write": "Editar regras de alerta",
    "notifications.write": "Editar canais de notificação",
    "profiles.read": "Ver perfis de modelos",
    "profiles.write": "Editar perfis de modelos",
    "releases.read": "Ver versões do coletor",
    "releases.publish": "Publicar versões do coletor",
    "settings.read": "Ver configurações",
    "settings.write": "Editar configurações",
    "permissions.write": "Editar a matriz de permissões",
}

ALL_PERMISSIONS: Final[frozenset[str]] = MATRIX_PERMISSIONS | frozenset(SPECIAL_PERMISSIONS)


def _module(*modules: str, actions: tuple[str, ...] = tuple(ACTIONS)) -> set[str]:
    return {f"{m}.{a}" for m in modules for a in actions}


_READ_OPERATIONAL = _module("customers", "agents", "devices", "reports", actions=("read",)) | {
    "alerts.read",
    "profiles.read",
    "releases.read",
}

ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    SUPERADMIN: ALL_PERMISSIONS,
    RESELLER_ADMIN: ALL_PERMISSIONS - {"resellers.write"},
    OPERATOR: frozenset(
        _READ_OPERATIONAL
        | _module("customers", "agents", "devices")
        | {
            SUPPLIES_MONITOR,
            "agents.command",
            "readings.adjust",
            "alerts.manage",
            "settings.read",
        }
    ),
    TECHNICIAN: frozenset(
        _READ_OPERATIONAL
        | {
            "devices.update",
            SUPPLIES_MONITOR,
            "agents.command",
            "devices.web_access",
            "alerts.manage",
            "profiles.write",
        }
    ),
    CUSTOMER_VIEWER: frozenset(
        _module("customers", "agents", "devices", "reports", actions=("read",)) | {"alerts.read"}
    ),
}

# Papéis cuja matriz a revenda pode ajustar; admin e superadmin têm sempre tudo (evita se trancar fora).
EDITABLE_ROLES: Final[tuple[str, ...]] = (OPERATOR, TECHNICIAN, CUSTOMER_VIEWER)


def grantable(role_code: str) -> frozenset[str]:
    """Matrix permissions a reseller may grant to the role (a customer user only reads)."""
    if role_code == CUSTOMER_VIEWER:
        return frozenset({f"{m}.read" for m in MODULES} | {SUPPLIES_MONITOR})
    return MATRIX_PERMISSIONS


def role_level(code: str) -> int:
    return ROLE_BY_CODE[code].level
