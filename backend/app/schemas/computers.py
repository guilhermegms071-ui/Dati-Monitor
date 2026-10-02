"""Computadores (PROMPT 11): PCs com coletor, impressoras USB e leitura manual."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ComputerOut(BaseModel):
    id: uuid.UUID
    name: str
    hostname: str | None
    os: str | None
    kind: str
    version: str | None
    state: str
    last_seen_at: datetime | None
    public_ip: str | None
    local_ips: list[str]
    location: str = Field(description="Cliente / local")
    usb_printers: int


class ComputerPage(BaseModel):
    items: list[ComputerOut]
    next_cursor: str | None


class UsbPrinterOut(BaseModel):
    id: uuid.UUID
    serial: str
    brand: str | None
    model: str | None
    name: str | None = Field(description="Nome no Windows e porta USB")
    status: str
    last_status_at: datetime | None
    discovery_state: str
    counter_available: bool = Field(description="A impressora já informou contador por PJL")
    last_total: int | None
    last_mono: int | None
    last_color: int | None
    last_read_at: datetime | None


class ManualReadingIn(BaseModel):
    total: int | None = Field(default=None, ge=0)
    mono: int | None = Field(default=None, ge=0)
    color: int | None = Field(default=None, ge=0)
    read_at: datetime | None = Field(default=None, description="Padrão: agora")
    note: str | None = Field(default=None, max_length=500)


class ManualReadingOut(BaseModel):
    reading_id: uuid.UUID
    read_at: datetime
    total: int | None
