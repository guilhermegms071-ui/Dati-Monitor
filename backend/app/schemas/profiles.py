"""Schemas of the Perfis de modelos screen (PROMPT 6.6)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProfileSummary(BaseModel):
    key: str
    description: str | None
    active_version: int | None
    latest_version: int
    source: str | None = Field(description="file = arquivo do repositório; portal = publicado nesta tela")
    sys_object_id_prefix: str | None
    placeholders: int = Field(description="OIDs ainda PREENCHER_PELO_WALK")
    updated_at: datetime | None


class ProfileVersionOut(BaseModel):
    version: int
    source: str
    active: bool
    created_at: datetime
    created_by: str | None
    notes: str | None


class ProfileDetail(BaseModel):
    key: str
    active_version: int | None
    yaml: str
    versions: list[ProfileVersionOut]


class ProfileText(BaseModel):
    yaml: str = Field(min_length=10, max_length=200_000)
    notes: str | None = Field(default=None, max_length=500)


class ProfileValidation(BaseModel):
    ok: bool
    error: str | None
    profile: dict[str, Any] | None = Field(
        description="Perfil validado (JSON), pronto para testar no coletor"
    )


class ActivateIn(BaseModel):
    version: int = Field(ge=1)


class WalkRow(BaseModel):
    oid: str
    type: str
    value: str


class WalkTree(BaseModel):
    walk_id: uuid.UUID
    ip: str
    total: int
    oid_count: int
    items: list[WalkRow]


class FixtureIn(BaseModel):
    name: str = Field(min_length=3, max_length=81)
    profile: str | None = Field(default=None, max_length=100, description="Perfil que deve ser escolhido")
    serial: str | None = Field(default=None, max_length=100)
    counters: dict[str, int] = Field(
        default_factory=dict, description="Valores da folha de contadores impressa no momento do walk"
    )


class FixtureOut(BaseModel):
    file: str
