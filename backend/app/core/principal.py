"""The authenticated portal user and the central tenant-scope filters (PROMPT section 7).

Every service query that touches tenant data must apply `reseller_scope` (and, where the entity belongs
to a customer, `customer_scope`). Superadmins see every reseller; customer-scoped users only their customer.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, true
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import forbidden
from app.core.permissions import SUPERADMIN, role_level
from app.core.security import LimitedReason


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    reseller_id: uuid.UUID
    role: str
    customer_id: uuid.UUID | None
    permissions: frozenset[str] = field(default_factory=frozenset)
    limited: LimitedReason | None = None
    email: str = ""
    ip: str | None = None

    @property
    def is_superadmin(self) -> bool:
        return self.role == SUPERADMIN

    @property
    def level(self) -> int:
        return role_level(self.role)

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise forbidden()

    def can_access_reseller(self, reseller_id: uuid.UUID) -> bool:
        return self.is_superadmin or reseller_id == self.reseller_id

    def can_access_customer(self, reseller_id: uuid.UUID, customer_id: uuid.UUID | None) -> bool:
        if not self.can_access_reseller(reseller_id):
            return False
        return self.customer_id is None or self.customer_id == customer_id


def reseller_scope(principal: Principal, column: InstrumentedAttribute[uuid.UUID]) -> ColumnElement[bool]:
    return true() if principal.is_superadmin else column == principal.reseller_id


def customer_scope(principal: Principal, column: InstrumentedAttribute[uuid.UUID]) -> ColumnElement[bool]:
    return true() if principal.customer_id is None else column == principal.customer_id
