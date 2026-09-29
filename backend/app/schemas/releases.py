"""Portal schemas for signed releases (PROMPT 5.2) and the cluster's preferred MASTER."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


class ReleaseOut(ORMModel):
    model_config = ConfigDict(from_attributes=True, json_schema_serialization_defaults_required=True)

    id: uuid.UUID
    component: str
    version: str
    os: str
    arch: str
    size_bytes: int
    sha256: str
    channel: str
    notes: str | None
    rollout_percent: int
    published_at: datetime
    yanked: bool
    updates_succeeded: int = 0
    updates_failed: int = 0
    updates_in_progress: int = 0
    canary_failure_percent: float = Field(default=0, description="Falhas de atualização em coletores canary")
    auto_update_blocked: bool = Field(
        default=False, description="Taxa de falha no canary acima do limite: fora da atualização automática"
    )


class ReleaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["canary", "stable"] | None = None
    rollout_percent: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=4000)
    yanked: bool | None = Field(default=None, description="Retirar a versão (não é mais instalada)")


class PreferredMasterIn(BaseModel):
    agent_id: uuid.UUID | None = Field(
        description="Coletor fixado como MASTER do local; vazio = sem preferência"
    )
