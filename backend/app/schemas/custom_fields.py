"""Schemas of device custom fields (PROMPT 16.7)."""

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

FieldType = Literal["text", "number", "date"]


class CustomFieldIn(BaseModel):
    key: str = Field(min_length=1, max_length=64, description="Minúsculas, números e _ (ex.: contrato)")
    label: str = Field(min_length=1, max_length=100)
    field_type: FieldType = "text"
    position: int = Field(default=0, ge=0, le=1000)
    active: bool = True

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", v):
            raise ValueError("use letras minúsculas, números e _, começando por letra")
        return v


class CustomFieldUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    position: int | None = Field(default=None, ge=0, le=1000)
    active: bool | None = None


class CustomFieldOut(ORMModel):
    id: uuid.UUID
    key: str
    label: str
    field_type: str
    position: int
    active: bool
    created_at: datetime
    updated_at: datetime
