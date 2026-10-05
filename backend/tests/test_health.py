import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from app.api.main import create_app
from app.core.config import Settings
from app.gateway.main import create_app as create_gateway
from tests.conftest import PYPROJECT_VERSION


def create_api(settings: Settings) -> FastAPI:
    return create_app(settings, run_bootstrap=False)


@asynccontextmanager
async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.parametrize(
    ("factory", "path", "service"),
    [(create_api, "/api/health", "api"), (create_gateway, "/health", "gateway")],
)
async def test_health_ok_with_real_database(
    test_settings: Settings, factory: Callable[[Settings], FastAPI], path: str, service: str
) -> None:
    async with client_for(factory(test_settings)) as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json; charset=utf-8"
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == service
    assert body["product"] == "Dati Monitor"
    assert body["version"] == PYPROJECT_VERSION
    assert body["database"]["ok"] is True
    assert body["database"]["server_version"].startswith("16")
    assert body["database"]["error"] is None


@pytest.mark.parametrize(("factory", "path"), [(create_api, "/api/health"), (create_gateway, "/health")])
async def test_health_reports_database_down(
    unreachable_settings: Settings,
    caplog: pytest.LogCaptureFixture,
    factory: Callable[[Settings], FastAPI],
    path: str,
) -> None:
    app = factory(unreachable_settings)
    logging.getLogger().addHandler(caplog.handler)
    async with client_for(app) as client:
        resp = await client.get(path)
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["database"]["ok"] is False
    assert body["database"]["error"]
    assert any("banco indisponível" in r.getMessage() for r in caplog.records)


async def test_api_cors_allows_portal_origin(test_settings: Settings) -> None:
    async with client_for(create_api(test_settings)) as client:
        resp = await client.options(
            "/api/health",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
        )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
