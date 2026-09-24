"""User management and audit log schemas."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.permissions import ROLE_BY_CODE
from app.core.validators import check_password_policy, normalize_email
from app.schemas.common import ORMModel


def _role(v: str | None) -> str | None:
    if v is not None and v not in ROLE_BY_CODE:
        raise ValueError(f"papel desconhecido: {v}")
    return v


class UserIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: str
    role: str
    customer_id: uuid.UUID | None = None
    reseller_id: uuid.UUID | None = Field(default=None, description="Somente superadmin; padrão: sua revenda")
    password: str | None = Field(default=None, description="Vazio: gera senha temporária")
    active: bool = True

    validate_email = field_validator("email")(normalize_email)
    validate_role = field_validator("role")(_role)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str | None) -> str | None:
        return check_password_policy(v) if v is not None else None


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    email: str | None = None
    role: str | None = None
    customer_id: uuid.UUID | None = None
    clear_customer: bool = Field(default=False, description="Remove o escopo de cliente")
    active: bool | None = None
    unlock: bool = Field(default=False, description="Desbloqueia após excesso de tentativas")

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return normalize_email(v) if v is not None else None

    validate_role = field_validator("role")(_role)


class UserOut(ORMModel):
    id: uuid.UUID
    reseller_id: uuid.UUID
    customer_id: uuid.UUID | None
    name: str
    email: str
    role_code: str
    active: bool
    totp_enabled: bool
    must_change_password: bool
    last_login_at: datetime | None
    locked_until: datetime | None
    created_at: datetime
    updated_at: datetime


class UserCreated(BaseModel):
    user: UserOut
    temporary_password: str | None = None


class ResetPasswordAdminRequest(BaseModel):
    mode: Literal["temporary", "email"] = "temporary"


class ResetPasswordAdminResponse(BaseModel):
    mode: Literal["temporary", "email"]
    temporary_password: str | None = None


class RoleOut(BaseModel):
    code: str
    name: str
    level: int
    permissions: list[str]


class AuditOut(ORMModel):
    id: uuid.UUID
    reseller_id: uuid.UUID | None
    user_id: uuid.UUID | None
    user_email: str | None = None
    action: str
    entity: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None
    created_at: datetime
