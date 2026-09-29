"""Schemas of Equipamentos > Descobertas (PROMPT 16.1)."""

import uuid
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
