"""Park screen (PROMPT 10.6), device detail and dashboard schemas."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.collection import DeviceOut
from app.schemas.tenancy import TonerThresholds


class SupplyLevel(BaseModel):
    """One bar of the "Níveis" column."""

    color: str
    percent: float | None
    level_state: str
    description: str | None


class ParkRow(DeviceOut):
    # O servidor sempre envia os campos com default: no OpenAPI de resposta eles são obrigatórios.
    model_config = ConfigDict(from_attributes=True, json_schema_serialization_defaults_required=True)

    customer_name: str
    site_name: str
    agent_name: str | None
    supplies: list[SupplyLevel] = Field(default_factory=list)


class ParkPage(BaseModel):
    items: list[ParkRow]
    next_cursor: str | None
    total: int = Field(description="Quantidade de equipamentos com os filtros aplicados")


class ParkCounts(BaseModel):
    total: int
    disconnected: int
    inactive: int


DeviceStatus = Literal[
    "ready", "printing", "warmup", "energy_saving", "warning", "error", "offline", "unknown"
]


class DeviceUpdate(BaseModel):
    asset_tag: str | None = Field(default=None, max_length=100)
    sector: str | None = Field(
        default=None, max_length=200, description="Vazio volta a seguir o sysLocation da impressora"
    )
    notes: str | None = Field(default=None, max_length=4000)
    alt_serial: str | None = Field(default=None, max_length=128)
    franchise_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    franchise_pages_mono: int | None = Field(default=None, ge=0)
    franchise_pages_color: int | None = Field(default=None, ge=0)
    overage_price_mono: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=4)
    overage_price_color: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=4)
    custom_fields: dict[str, str | float | None] | None = Field(
        default=None, description="Valores dos campos personalizados (chave → valor; vazio remove)"
    )
    toner_mode: Literal["off", "global", "individual"] | None = None
    toner_thresholds: TonerThresholds | None = None
    monitored: bool | None = None
    active: bool | None = None
    site_id: uuid.UUID | None = Field(
        default=None, description="Mover para outro local (o cliente acompanha)"
    )


BulkAction = Literal["activate", "deactivate", "monitor", "unmonitor", "update", "move", "read_now"]


class BulkDevicesIn(BaseModel):
    device_ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)
    action: BulkAction
    sector: str | None = Field(default=None, max_length=200)
    asset_tag: str | None = Field(default=None, max_length=100)
    site_id: uuid.UUID | None = None


class BulkDevicesOut(BaseModel):
    # O servidor sempre envia os campos com default: no OpenAPI de resposta eles são obrigatórios.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    changed: int
    commands: list[uuid.UUID] = Field(default_factory=list, description="Comandos read_now criados")
    skipped: list[dict[str, Any]] = Field(default_factory=list)


class CounterPoint(BaseModel):
    period: date
    total: int | None
    mono: int | None
    color: int | None
    pages: int | None = Field(description="Páginas no período (diferença para o período anterior)")
    pages_mono: int | None
    pages_color: int | None


class SupplyPoint(BaseModel):
    read_at: datetime
    supply_key: str
    color: str | None
    percent: Decimal | None
    level_state: str


class AdjustmentIn(BaseModel):
    reading_id: uuid.UUID
    read_at: datetime
    total: int | None = Field(default=None, ge=0)
    mono: int | None = Field(default=None, ge=0)
    color: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=5, max_length=2000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("informe o motivo do ajuste")
        return v.strip()


class AdjustmentOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    reading_id: uuid.UUID
    read_at: datetime
    total: int | None
    mono: int | None
    color: int | None
    reason: str
    user_id: uuid.UUID
    user_name: str | None
    created_at: datetime


# ----------------------------------------------------------------------------- dashboard


class DashboardCards(BaseModel):
    devices_monitored: int
    devices_online: int
    devices_disconnected: int
    agents_online: int
    agents_offline: int
    alerts_open: int
    toners_critical: int


class PagesPerDay(BaseModel):
    day: date
    mono: int
    color: int


class OfflineAgent(BaseModel):
    id: uuid.UUID
    name: str
    customer_name: str
    site_name: str
    last_seen_at: datetime | None
    hostname: str | None


class CriticalSupply(BaseModel):
    device_id: uuid.UUID
    serial: str
    model: str | None
    customer_name: str
    color: str | None
    description: str | None
    percent: Decimal | None
    days_to_empty: Decimal | None
    days_to_empty_min: Decimal | None = None
    days_to_empty_max: Decimal | None = None
    forecast_confidence: Decimal | None = None


class TonersByColor(BaseModel):
    """Toners previstos para acabar em até 30 dias, por cor (16.13); só previsões confiáveis."""

    black: int
    cyan: int
    magenta: int
    yellow: int


class Dashboard(BaseModel):
    cards: DashboardCards
    pages_per_day: list[PagesPerDay]
    offline_agents: list[OfflineAgent]
    critical_supplies: list[CriticalSupply]
    ending_7_days: list[CriticalSupply] = Field(
        description="Toners que acabam em até 7 dias (previsão confiável)"
    )
    ending_30_days_by_color: TonersByColor
