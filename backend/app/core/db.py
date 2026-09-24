"""Async SQLAlchemy engine factory and a connectivity probe."""

import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, connect_args={"timeout": 5})


@dataclass(frozen=True)
class DbStatus:
    ok: bool
    latency_ms: float | None
    server_version: str | None
    error: str | None


async def check_database(engine: AsyncEngine) -> DbStatus:
    """Runs a real round-trip to PostgreSQL. Never raises: failures are returned in `error`."""
    started = time.perf_counter()
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SHOW server_version"))).scalar_one()
    except Exception as exc:  # reported to the caller in DbStatus.error, never swallowed
        return DbStatus(ok=False, latency_ms=None, server_version=None, error=f"{type(exc).__name__}: {exc}")
    return DbStatus(
        ok=True,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        server_version=str(version),
        error=None,
    )
