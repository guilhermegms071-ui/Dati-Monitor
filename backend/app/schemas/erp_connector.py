"""Conector do Dataclassic (PROMPT 16.11): company-wide parameters and the queue shown in the portal."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validators import normalize_email

OsAlertType = Literal["service_call", "consumable", "jam_recurrent", "parts", "other"]


class SupplyRequestParams(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    enabled: bool = False
    operation: str | None = Field(default=None, max_length=50, description="Operação")
    desc_type: str | None = Field(default=None, max_length=50, description="Tipo desc")
    status: str | None = Field(default=None, max_length=50)
    situation: str | None = Field(default=None, max_length=50, description="Situação")
    payment_condition: str | None = Field(default=None, max_length=50, description="Condição de pagamento")
    seller: str | None = Field(default=None, max_length=50, description="Vendedor")
    freight_type: str | None = Field(default=None, max_length=50, description="Tipo de frete")
    notify_email: str | None = Field(default=None, description="E-mail de notificação")
    email_only: bool = Field(default=False, description="Apenas enviar e-mail (não cria no ERP)")

    @field_validator("notify_email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return normalize_email(v) if v and v.strip() else None


def _default_os_types() -> list[OsAlertType]:
    return ["service_call"]


class ServiceOrderParams(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    enabled: bool = False
    technician_code: str | None = Field(default=None, max_length=50, description="Código do técnico")
    reason: str | None = Field(default=None, max_length=100, description="Motivo")
    intervention_type: str | None = Field(default=None, max_length=50, description="Tipo de intervenção")
    status: str | None = Field(default=None, max_length=50)
    alert_types: list[OsAlertType] = Field(
        default_factory=_default_os_types, description="Quais alertas viram OS"
    )
    prt_alert_codes: list[int] = Field(
        default_factory=list,
        description="Códigos prtAlert importáveis (vazio = todos); vale para os alertas da impressora",
    )

    @field_validator("prt_alert_codes")
    @classmethod
    def _codes(cls, v: list[int]) -> list[int]:
        if any(c < 1 or c > 100_000 for c in v):  # noqa: PLR2004
            raise ValueError("código prtAlert fora da faixa")
        return sorted(set(v))


class TransportParams(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    kind: Literal["file", "http"] = Field(
        default="file", description="file = pasta monitorada pelo ERP; http = POST JSON para a API do ERP"
    )
    directory: str | None = Field(default=None, max_length=500, description="Pasta (transporte arquivo)")
    url: str | None = Field(default=None, max_length=500, description="URL (transporte http)")
    auth_header: str | None = Field(
        default=None, max_length=1000, description="Valor do cabeçalho Authorization (fica cifrado)"
    )

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("a URL precisa começar com http:// ou https://")
        return v


class ErpConnectorSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)

    enabled: bool = False
    company_code: str | None = Field(default=None, max_length=50, description="Código da empresa")
    operator: str | None = Field(default=None, max_length=50, description="Operador")
    send_counters: bool = Field(default=False, description="Enviar contadores (leitura de corte diária)")
    counters_hour: int = Field(default=6, ge=0, le=23, description="Hora do envio diário (Brasília)")
    supply_request: SupplyRequestParams = Field(default_factory=SupplyRequestParams)
    service_order: ServiceOrderParams = Field(default_factory=ServiceOrderParams)
    transport: TransportParams = Field(default_factory=TransportParams)
    enabled_since: datetime | None = Field(
        default=None, description="Desde quando está ligado: alertas anteriores não são enviados"
    )


class ErpQueueItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: Literal["counters", "supply_request", "service_order"]
    status: Literal["pending", "sent", "error"]
    summary: str
    device_id: uuid.UUID | None
    alert_id: uuid.UUID | None
    attempts: int
    last_error: str | None
    next_attempt_at: datetime
    sent_at: datetime | None
    delivered_via: str | None
    created_at: datetime


class ErpQueueDetail(ErpQueueItemOut):
    payload: dict[str, Any]


class ErpQueuePage(BaseModel):
    items: list[ErpQueueItemOut]
    next_cursor: str | None


class ErpQueueCounts(BaseModel):
    pending: int
    sent: int
    error: int


class ErpRetryIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class ErpRetryOut(BaseModel):
    requeued: int
