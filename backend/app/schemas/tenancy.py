"""Schemas for resellers, companies, customers and sites."""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.validators import check_timezone, normalize_cnpj, normalize_email
from app.schemas.collection import CollectionConfig
from app.schemas.common import ORMModel


def _cnpj(v: str | None) -> str | None:
    return normalize_cnpj(v)


def _opt_email(v: str | None) -> str | None:
    return normalize_email(v) if v else None


UFS = frozenset(
    [
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    ]
)


def _cep(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    digits = re.sub(r"\D", "", v)
    if len(digits) != 8:  # noqa: PLR2004 - CEP tem 8 dígitos
        raise ValueError("CEP deve ter 8 dígitos")
    return digits


def _uf(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    uf = v.strip().upper()
    if uf not in UFS:
        raise ValueError("UF inválida")
    return uf


class TonerThresholds(BaseModel):
    """Limiar de toner por cor, em % (seção 16.5)."""

    black: int = Field(default=10, ge=0, le=100)
    cyan: int = Field(default=10, ge=0, le=100)
    magenta: int = Field(default=10, ge=0, le=100)
    yellow: int = Field(default=10, ge=0, le=100)


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
    toner_monitoring: bool = True
    toner_thresholds: TonerThresholds = Field(default_factory=TonerThresholds)

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
    toner_monitoring: bool | None = None
    toner_thresholds: TonerThresholds | None = None

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
    toner_monitoring: bool
    toner_thresholds: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SiteAddress(BaseModel):
    """Endereço completo do Local (seção 16.9); o portal preenche pelo CEP (ViaCEP)."""

    cep: str | None = None
    street: str | None = Field(default=None, max_length=300)
    number: str | None = Field(default=None, max_length=30)
    complement: str | None = Field(default=None, max_length=200)
    district: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    state: str | None = None
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, decimal_places=6)

    validate_cep = field_validator("cep")(_cep)
    validate_uf = field_validator("state")(_uf)


class SiteIn(SiteAddress):
    customer_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    timezone: str = "America/Sao_Paulo"
    auto_activate_devices: bool = False
    collection_config: CollectionConfig = Field(default_factory=CollectionConfig)

    validate_tz = field_validator("timezone")(check_timezone)


class SiteUpdate(SiteAddress):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = None
    auto_activate_devices: bool | None = None
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
    cep: str | None
    street: str | None
    number: str | None
    complement: str | None
    district: str | None
    city: str | None
    state: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    timezone: str
    auto_activate_devices: bool
    collection_config: dict[str, Any]
    master_agent_id: uuid.UUID | None
    preferred_master_agent_id: uuid.UUID | None
    master_lease_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
