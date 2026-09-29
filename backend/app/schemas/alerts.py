"""Schemas of alerts, alert rules, notification channels and the notification log (PROMPT 8/9/16)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import ORMModel

Severity = Literal["info", "warning", "critical"]
RuleType = Literal[
    "toner_low",
    "toner_days_left",
    "device_no_reading",
    "agent_offline",
    "counter_regression",
    "suspicious_jump",
    "sum_mismatch",
    "hardware_error",
    "paper_jam",
    "door_open",
    "jam_recurrent",
    "printer_alert",
]
PrinterAlertCategory = Literal["parts", "service_call", "jam", "consumable", "other"]


# ----------------------------------------------------------------------------- parâmetros por tipo


class AgentOfflineParams(BaseModel):
    minutes: int = Field(default=5, ge=0, le=1440, description="Sem sinal há quantos minutos")


class NoReadingParams(BaseModel):
    hours: int = Field(default=6, ge=1, le=720)


class DaysLeftParams(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    min_confidence: float = Field(default=0.5, ge=0, le=1, description="Previsão menos confiável não alerta")


class HardwareErrorParams(BaseModel):
    flags: list[str] = Field(
        default_factory=lambda: ["serviceRequested"],
        description="Bits de hrPrinterDetectedErrorState que contam como erro de hardware",
    )


class JamRecurrentParams(BaseModel):
    count: int = Field(default=5, ge=2, le=100)
    days: int = Field(default=3, ge=1, le=60)


def _service_call() -> list[PrinterAlertCategory]:
    return ["service_call"]


class PrinterAlertParams(BaseModel):
    categories: list[PrinterAlertCategory] = Field(default_factory=_service_call, min_length=1)


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


PARAMS_BY_TYPE: dict[str, type[BaseModel]] = {
    "agent_offline": AgentOfflineParams,
    "device_no_reading": NoReadingParams,
    "toner_days_left": DaysLeftParams,
    "hardware_error": HardwareErrorParams,
    "jam_recurrent": JamRecurrentParams,
    "printer_alert": PrinterAlertParams,
    "toner_low": NoParams,  # limiares por cor vêm do cliente/equipamento (seção 16.5)
    "paper_jam": NoParams,
    "door_open": NoParams,
    "counter_regression": NoParams,
    "suspicious_jump": NoParams,
    "sum_mismatch": NoParams,
}

RULE_LABELS: dict[str, str] = {
    "agent_offline": "Coletor sem sinal",
    "device_no_reading": "Equipamento sem leitura",
    "toner_low": "Toner abaixo do limiar",
    "toner_days_left": "Toner acaba em poucos dias",
    "hardware_error": "Erro de hardware",
    "paper_jam": "Atolamento de papel",
    "door_open": "Porta aberta",
    "jam_recurrent": "Atolamento recorrente",
    "printer_alert": "Alerta da impressora",
    "counter_regression": "Contador regrediu",
    "suspicious_jump": "Salto suspeito de contador",
    "sum_mismatch": "PB + cor diferente do total",
}


def validate_params(rule_type: str, params: dict[str, Any]) -> dict[str, Any]:
    model = PARAMS_BY_TYPE[rule_type]
    return model.model_validate(params).model_dump()


# ----------------------------------------------------------------------------- regras


class AlertRuleIn(BaseModel):
    customer_id: uuid.UUID | None = Field(default=None, description="Vazio = vale para toda a revenda")
    name: str = Field(min_length=2, max_length=200)
    type: RuleType
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = "warning"
    enabled: bool = True
    channel_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=20, description="Vazio = todos os canais ativos da revenda"
    )


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    params: dict[str, Any] | None = None
    severity: Severity | None = None
    enabled: bool | None = None
    channel_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)


class AlertRuleOut(ORMModel):
    id: uuid.UUID
    reseller_id: uuid.UUID
    customer_id: uuid.UUID | None
    customer_name: str | None = None
    name: str
    type: str
    type_label: str = ""
    params: dict[str, Any]
    severity: str
    enabled: bool
    channel_ids: list[Any]
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------------------- alertas


class AlertOut(ORMModel):
    model_config = ConfigDict(from_attributes=True, json_schema_serialization_defaults_required=True)

    id: uuid.UUID
    reseller_id: uuid.UUID
    customer_id: uuid.UUID | None
    site_id: uuid.UUID | None
    rule_id: uuid.UUID | None
    type: str
    type_label: str = ""
    severity: str
    target_type: str
    target_id: uuid.UUID
    target_name: str = ""
    customer_name: str = ""
    state: str
    message: str
    data: dict[str, Any]
    opened_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: uuid.UUID | None
    resolved_at: datetime | None
    resolved_by: uuid.UUID | None


class AlertPage(BaseModel):
    items: list[AlertOut]
    next_cursor: str | None
    total: int


class AlertCounts(BaseModel):
    open: int
    critical: int
    warning: int
    info: int


class AlertActionIn(BaseModel):
    alert_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    action: Literal["acknowledge", "resolve"]


class AlertActionOut(BaseModel):
    changed: int


# ----------------------------------------------------------------------------- canais e notificações

ChannelKind = Literal["email", "whatsapp", "webhook"]
SECRET_KEYS = frozenset({"password", "access_token", "secret"})


class ChannelIn(BaseModel):
    kind: ChannelKind
    name: str = Field(min_length=2, max_length=200)
    config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "email: smtp_host/smtp_port/username/password/starttls/from (opcionais), daily_summary; "
            "webhook: url, secret, headers; whatsapp: provider meta (phone_number_id, access_token) "
            "ou generic (url_template, method, body_template, headers)"
        ),
    )
    recipients: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True

    @field_validator("recipients")
    @classmethod
    def _strip(cls, v: list[str]) -> list[str]:
        return [r.strip() for r in v if r.strip()]


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    config: dict[str, Any] | None = Field(
        default=None, description="Segredos omitidos ou com '••••' mantêm o valor guardado"
    )
    recipients: list[str] | None = Field(default=None, max_length=100)
    enabled: bool | None = None


class ChannelOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    config: dict[str, Any] = Field(description="Segredos aparecem mascarados (••••)")
    recipients: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ChannelTestOut(BaseModel):
    ok: bool
    results: list[dict[str, Any]]


class NotificationOut(ORMModel):
    id: uuid.UUID
    channel_id: uuid.UUID | None
    alert_id: uuid.UUID | None
    kind: str
    destination: str
    subject: str
    status: str
    error: str | None
    attempts: int
    next_attempt_at: datetime
    sent_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    next_cursor: str | None


class QuietHours(BaseModel):
    """Silêncio (seção 9): fora do horário, só alertas críticos saem na hora; os outros esperam o fim."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    enabled: bool = True
    start_hour: int = Field(default=22, ge=0, le=23)
    end_hour: int = Field(default=7, ge=0, le=23)


class NotificationSettings(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    daily_summary: bool = Field(default=True, description="Resumo diário às 07:00 nos canais de e-mail")
    replacement_threshold_points: int = Field(
        default=20, ge=5, le=90, description="Subida de nível que conta como troca de suprimento (16.3)"
    )


# ----------------------------------------------------------------------------- suprimentos e impressora


class SupplyReplacementOut(ORMModel):
    id: uuid.UUID
    device_id: uuid.UUID
    serial: str = ""
    model: str | None = None
    customer_name: str = ""
    supply_key: str
    description: str | None
    supply_type: str | None
    color: str | None
    replaced_at: datetime
    previous_read_at: datetime
    level_before: Decimal
    level_after: Decimal
    total_before: int | None
    total_after: int | None
    color_before: int | None
    color_after: int | None
    yield_pages: int | None
    yield_counter: str | None
    nominal_capacity: int | None
    premature: bool
    cartridge_serial_before: str | None
    cartridge_serial_after: str | None


class SupplyReplacementPage(BaseModel):
    items: list[SupplyReplacementOut]
    next_cursor: str | None
    total: int


class PrinterAlertOut(ORMModel):
    id: uuid.UUID
    device_id: uuid.UUID
    serial: str = ""
    model: str | None = None
    customer_name: str = ""
    category: str
    severity: int
    training_level: int | None
    group: int | None
    code: int
    description: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    cleared_at: datetime | None
    total_at: int | None
    mono_at: int | None
    color_at: int | None


class PrinterAlertPage(BaseModel):
    items: list[PrinterAlertOut]
    next_cursor: str | None
    total: int


class PrinterAlertCounts(BaseModel):
    parts: int
    service_call: int
    jam: int
    consumable: int
    other: int
