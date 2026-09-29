"""Portal/agent REST API process (port 8000 in development)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.agent import routes as agent_routes
from app.api.v1 import alerts as alerts_routes
from app.api.v1 import auth as auth_routes
from app.api.v1 import collection as collection_routes
from app.api.v1 import commands as commands_routes
from app.api.v1 import discovery as discovery_routes
from app.api.v1 import park as park_routes
from app.api.v1 import releases as releases_routes
from app.api.v1 import tenancy as tenancy_routes
from app.api.v1 import users as users_routes
from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.errors import install_error_handlers
from app.core.health import health_router
from app.core.logging import configure_logging
from app.core.product import get_product
from app.core.ratelimit import RateLimiter
from app.core.responses import UTF8JSONResponse
from app.core.version import backend_version
from app.schemas import agent as proto
from app.services.agents import NonceCache
from app.services.bootstrap import announce_bootstrap, ensure_bootstrap
from app.services.catalog import sync_brands, sync_profiles
from app.services.live import LiveBroker


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def _publish_raw_body_models(app: FastAPI) -> None:
    """The readings endpoint reads its (gzip) body by hand; its model is published in
    components/schemas so the OpenAPI stays valid and the TypeScript client can use it."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        comps = schema.setdefault("components", {}).setdefault("schemas", {})
        model = proto.ReadingsRequest.model_json_schema(ref_template="#/components/schemas/{model}")
        for name, sub in model.pop("$defs", {}).items():
            comps.setdefault(name, sub)
        comps.setdefault("ReadingsRequest", model)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def create_app(settings: Settings | None = None, *, run_bootstrap: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(app.state.engine)
        app.state.login_limiter = RateLimiter(settings.login_rate_limit_per_minute, 60)
        app.state.agent_limiter = RateLimiter(settings.agent_rate_limit_per_minute, 60)
        app.state.agent_nonces = NonceCache()
        app.state.live = LiveBroker(settings.database_url)
        app.state.live.start()
        try:
            if run_bootstrap:
                async with app.state.sessionmaker() as session:
                    result = await ensure_bootstrap(session, settings)
                    await sync_brands(session)
                    await sync_profiles(session)
                    await session.commit()
                announce_bootstrap(result)
            yield
        finally:
            await app.state.live.stop()
            await app.state.engine.dispose()

    app = FastAPI(
        title=f"{get_product().name} API",
        version=backend_version(),
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
        description="API do portal, dos coletores e da integração com o ERP.",
    )
    install_error_handlers(app)
    _publish_raw_body_models(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cache-Control", "no-store")
        if settings.app_env == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    app.include_router(health_router("/api/health", "api", _engine))
    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(auth_routes.router)
    v1.include_router(tenancy_routes.router)
    v1.include_router(users_routes.router)
    v1.include_router(collection_routes.router)
    v1.include_router(commands_routes.router)
    v1.include_router(park_routes.router)
    v1.include_router(releases_routes.router)
    v1.include_router(discovery_routes.router)
    v1.include_router(alerts_routes.router)
    app.include_router(v1)
    app.include_router(agent_routes.router)
    app.include_router(agent_routes.watchdog_router)
    return app
