import asyncio
import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.db import make_engine, make_sessionmaker
from app.services.partitions import month_starts
from app.worker.main import DbWatch, WorkerContext, build_scheduler, db_check_job, partitions_job, run


@pytest.fixture(autouse=True)
def _capture_root(caplog: pytest.LogCaptureFixture) -> None:
    logging.getLogger().addHandler(caplog.handler)
    caplog.set_level(logging.INFO)


async def test_db_check_job_ok_logs_transition_once(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    watch = DbWatch()
    first = await db_check_job(engine, watch)
    second = await db_check_job(engine, watch)
    assert first.ok
    assert second.ok
    assert watch.last_ok is True
    assert sum("banco acessível" in r.getMessage() for r in caplog.records) == 1


async def test_db_check_job_failure_is_logged_every_time(
    unreachable_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    eng = make_engine(unreachable_settings.database_url)
    watch = DbWatch()
    try:
        await db_check_job(eng, watch)
        await db_check_job(eng, watch)
    finally:
        await eng.dispose()
    assert watch.last_ok is False
    errors = [
        r for r in caplog.records if r.levelno == logging.ERROR and "banco indisponível" in r.getMessage()
    ]
    assert len(errors) == 2


async def test_scheduler_registers_jobs(engine: AsyncEngine, test_settings: Settings) -> None:
    ctx = WorkerContext(settings=test_settings, engine=engine, sessionmaker=make_sessionmaker(engine))
    scheduler = build_scheduler(ctx)
    job = scheduler.get_job("db_check")
    assert job is not None
    assert job.trigger.interval.total_seconds() == test_settings.db_check_interval_seconds
    assert scheduler.get_job("partitions") is not None


@pytest.mark.usefixtures("clean_db")
async def test_partitions_job_keeps_window(engine: AsyncEngine, test_settings: Settings) -> None:
    ctx = WorkerContext(settings=test_settings, engine=engine, sessionmaker=make_sessionmaker(engine))
    await partitions_job(ctx)
    async with engine.connect() as conn:
        names = set(
            (
                await conn.execute(text("SELECT relname FROM pg_class WHERE relname LIKE 'readings_20%'"))
            ).scalars()
        )
    for month in month_starts(datetime.now(UTC).date(), 1, 3):
        assert f"readings_{month:%Y_%m}" in names


async def test_run_executes_scheduled_checks_and_stops(
    test_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(run(test_settings, stop))
    await asyncio.sleep(2.5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    messages = [r.getMessage() for r in caplog.records]
    assert "worker iniciado" in messages
    assert "worker encerrado" in messages
