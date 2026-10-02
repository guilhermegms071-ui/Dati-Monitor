"""Acesso remoto à página web da impressora (PROMPT 4.9)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class WebSessionIn(BaseModel):
    port: int = Field(default=80, description="80, 443, 8000, 8080 ou 8443")
    scheme: Literal["http", "https"] = "http"


class WebSessionByIpIn(WebSessionIn):
    ip: str = Field(min_length=7, max_length=64, description="IP digitado pelo técnico")


class WebSessionOut(BaseModel):
    id: uuid.UUID
    url: str = Field(description="Abra em nova aba; o link só vale para o primeiro navegador que o usar")
    device_id: uuid.UUID
    ip: str
    port: int
    scheme: str
    agent_name: str
    expires_at: datetime
