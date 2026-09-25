"""Schemas for resellers, companies, customers and sites."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.validators import check_timezone, normalize_cnpj, normalize_email
from app.schemas.collection import CollectionConfig
from app.schemas.common import ORMModel


def _cnpj(v: str | None) -> str | None:
    return normalize_cnpj(v)


def _opt_email(v: str | None) -> str | None:
    return normalize_email(v) if v else None


class ResellerIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    cnpj: str | None = None
    logo_url: str | None = Field(default=None, max_length=2000)

    validate_cnpj = field_validator("cnpj")(_cnpj)


class ResellerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    cnpj: str | None = None
    logo_url: str | None = Field(default=None, max_length=2000)

    validate_cnpj = field_validator("cnpj")(_cnpj)


class ResellerOut(ORMModel):
    id: uuid.UUID
    name: str
    cnpj: str | None
    logo_url: str | None
    created_at: datetime
    updated_at: datetime


class CompanyIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    cnpj: str | None = None
    reseller_id: uuid.UUID | None = Field(default=None, description="Somente superadmin; padrão: sua revenda")

    validate_cnpj = field_validator("cnpj")(_cnpj)


class CompanyUpdate(BaseModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=200)
    cnpj: str | None = None

    validate_cnpj = field_validator("cnpj")(_cnpj)


class CompanyOut(ORMModel):
    id: uuid.UUID
    reseller_id: uuid.UUID
    legal_name: str
    cnpj: str | None
    created_at: datetime
    updated_at: datetime


class CustomerIn(BaseModel):
    company_id: uuid.UUID
    name: str = Field(min_length=2, max_length=200)
    cnpj: str | None = None
    contact_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = None
    erp_code: str | None = Field(default=None, max_length=64)
    active: bool = True

    validate_cnpj = field_validator("cnpj")(_cnpj)
    validate_email = field_validator("email")(_opt_email)


class CustomerUpdate(BaseModel):
    company_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=2, max_length=200)
    cnpj: str | None = None
    contact_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = None
    erp_code: str | None = Field(default=None, max_length=64)
    active: bool | None = None

    validate_cnpj = field_validator("cnpj")(_cnpj)
    validate_email = field_validator("email")(_opt_email)


class CustomerOut(ORMModel):
    id: uuid.UUID
    reseller_id: uuid.UUID
    company_id: uuid.UUID
    name: str
    cnpj: str | None
    contact_name: str | None
    phone: str | None
    email: str | None
    erp_code: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class SiteIn(BaseModel):
    customer_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=2000)
    timezone: str = "America/Sao_Paulo"
    collection_config: CollectionConfig = Field(default_factory=CollectionConfig)

    validate_tz = field_validator("timezone")(check_timezone)


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=2000)
    timezone: str | None = None
    collection_config: CollectionConfig | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return check_timezone(v) if v is not None else None


class SiteOut(ORMModel):
    id: uuid.UUID
    reseller_id: uuid.UUID
    customer_id: uuid.UUID
    name: str
    address: str | None
    timezone: str
    collection_config: dict[str, Any]
    master_agent_id: uuid.UUID | None
    preferred_master_agent_id: uuid.UUID | None
    master_lease_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
