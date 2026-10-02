"""Downloads: instaladores do coletor (Fase 8)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class InstallerOut(BaseModel):
    id: uuid.UUID
    kind: Literal["windows", "deb", "tar"]
    arch: str = Field(description="all (setup.exe do Windows) ou amd64/386/arm64/arm")
    version: str
    filename: str
    size_bytes: int
    sha256: str
    notes: str | None
    withdrawn: bool
    latest: bool = Field(description="Versão oferecida hoje para este tipo/arquitetura")
    created_at: datetime
    published_by: str | None


class InstallerUpdate(BaseModel):
    withdrawn: bool
