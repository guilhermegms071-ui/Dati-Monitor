"""Portal/agent REST API process (port 8000 in development)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.core.db import make_engine
from app.core.health import health_router
from app.core.logging import configure_logging
from app.core.product import get_product
from app.core.version import backend_version


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = make_engine(settings.database_url)
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(title=f"{get_product().name} API", version=backend_version(), lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router("/api/health", "api", _engine))
    return app
