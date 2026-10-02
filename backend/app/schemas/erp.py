"""Read-only ERP API (PROMPT 7) and the integration tokens managed in the portal."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ErpTokenIn(BaseModel):
    name: str = Field(min_length=2, max_length=200, description="Ex.: Dataclassic produção")


class ErpTokenOut(BaseModel):
    id: uuid.UUID
    name: str
    token_prefix: str = Field(description="Início do token, para reconhecer qual é qual")
    created_at: datetime
    created_by: str | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class ErpTokenCreated(ErpTokenOut):
    token: str = Field(description="Token completo: mostrado só agora, guarde no ERP")


class ErpReading(BaseModel):
    reading_id: uuid.UUID
    read_at: datetime
    device_id: uuid.UUID
    serial: str
    customer_erp_code: str | None
    total: int | None
    mono: int | None
    color: int | None
    adjusted: bool = Field(description="Valores corrigidos por ajuste manual no portal")


class ErpReadingPage(BaseModel):
    items: list[ErpReading]
    next_cursor: str | None = Field(description="Passe em `cursor` para a próxima página; null = fim")


class ErpCutoffItem(BaseModel):
    device_id: uuid.UUID
    serial: str
    customer_erp_code: str | None
    reading_id: uuid.UUID
    read_at: datetime
    total: int | None
    mono: int | None
    color: int | None


class ErpCutoffResponse(BaseModel):
    date: date
    items: list[ErpCutoffItem]


class ErpDevice(BaseModel):
    device_id: uuid.UUID
    serial: str
    alt_serial: str | None
    asset_tag: str | None
    brand: str | None
    model: str | None
    is_color: bool | None
    sector: str | None
    active: bool
    customer_erp_code: str | None
    customer_name: str
    site_name: str
    franchise_value: Decimal | None
    franchise_pages_mono: int | None
    franchise_pages_color: int | None
    overage_price_mono: Decimal | None
    overage_price_color: Decimal | None
    last_read_at: datetime | None
    last_total: int | None
    last_mono: int | None
    last_color: int | None


class ErpDevicePage(BaseModel):
    items: list[ErpDevice]
    next_after: uuid.UUID | None = Field(description="Passe em `after` para a próxima página; null = fim")
