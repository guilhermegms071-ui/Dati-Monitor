"""Schemas of Equipamentos > Descobertas (PROMPT 16.1)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.park import ParkRow


class DiscoveryPage(BaseModel):
    items: list[ParkRow]
    next_cursor: str | None
    total: int


class DiscoveryCounts(BaseModel):
    pending: int
    discarded: int


class DecisionIn(BaseModel):
    device_ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)
    action: Literal["approve", "discard", "restore"] = Field(
        description="approve = Ativar; discard = Descartar; restore = voltar descartado para pendente"
    )


class DecisionOut(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    changed: int
    skipped: list[dict[str, Any]] = Field(default_factory=list)


class TransferOut(BaseModel):
    """Equipamento que apareceu num local de outro cliente e espera a decisão."""

    device_id: uuid.UUID
    serial: str
    model: str | None
    from_customer: str
    from_site: str
    to_customer: str
    to_site: str
    detected_at: datetime


class TransferPage(BaseModel):
    items: list[TransferOut]
    next_cursor: str | None
    total: int


class TransferDecisionIn(BaseModel):
    action: Literal["approve", "reject"] = Field(
        description="approve = vai para o novo cliente desde que apareceu lá; reject = fica no cliente atual"
    )
