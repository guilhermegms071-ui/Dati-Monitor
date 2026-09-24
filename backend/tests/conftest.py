"""Shared fixtures. Tests run against the real local PostgreSQL database dati_test."""

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.db import make_engine
from app.core.product import REPO_ROOT


def _load_repo_env() -> None:
    """Loads the repository .env into os.environ without overriding real environment variables."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_repo_env()


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.exit(
            "TEST_DATABASE_URL não definida (.env ou ambiente). Rode scripts\\setup-db.ps1.", returncode=2
        )
    return url


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(database_url=_test_database_url(), app_env="test", db_check_interval_seconds=1)


@pytest.fixture(scope="session")
def unreachable_settings() -> Settings:
    # Porta 1 do loopback: conexão recusada imediatamente, simulando banco fora do ar.
    return Settings(
        database_url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
        app_env="test",
        db_check_interval_seconds=1,
    )


@pytest.fixture
async def engine(test_settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(test_settings.database_url)
    yield eng
    await eng.dispose()
