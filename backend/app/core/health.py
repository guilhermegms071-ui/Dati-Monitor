"""Health endpoint shared by the API and the gateway processes."""

import logging
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import check_database
from app.core.product import get_product
from app.core.version import backend_version

logger = logging.getLogger(__name__)


class DatabaseHealth(BaseModel):
    ok: bool
    latency_ms: float | None
    server_version: str | None
    error: str | None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    product: str
    version: str
    database: DatabaseHealth


def health_router(path: str, service: str, engine_getter: Callable[[Request], AsyncEngine]) -> APIRouter:
    router = APIRouter()

    @router.get(path, response_model=HealthResponse, tags=["saúde"])
    async def health(request: Request, response: Response) -> HealthResponse:
        db = await check_database(engine_getter(request))
        if not db.ok:
            logger.error("health: banco indisponível: %s", db.error)
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ok" if db.ok else "degraded",
            service=service,
            product=get_product().name,
            version=backend_version(),
            database=DatabaseHealth(**db.__dict__),
        )

    return router
