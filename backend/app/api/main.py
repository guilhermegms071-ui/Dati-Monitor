"""Portal/agent REST API process (port 8000 in development)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.v1 import auth as auth_routes
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
from app.services.bootstrap import announce_bootstrap, ensure_bootstrap


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def create_app(settings: Settings | None = None, *, run_bootstrap: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(app.state.engine)
        app.state.login_limiter = RateLimiter(settings.login_rate_limit_per_minute, 60)
        try:
            if run_bootstrap:
                async with app.state.sessionmaker() as session:
                    result = await ensure_bootstrap(session, settings)
                    await session.commit()
                announce_bootstrap(result)
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(
        title=f"{get_product().name} API",
        version=backend_version(),
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
        description="API do portal, dos coletores e da integração com o ERP.",
    )
    install_error_handlers(app)
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
    app.include_router(v1)
    return app
