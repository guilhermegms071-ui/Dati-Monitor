import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.db import make_engine, make_sessionmaker
from app.models import Agent, AgentPresence, Command, Device
from app.services.partitions import month_starts
from app.worker.main import (
    DbWatch,
    WorkerContext,
    build_scheduler,
    commands_job,
    db_check_job,
    disconnected_job,
    partitions_job,
    presence_job,
    run,
)
from tests.conftest import Factory


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
    for job_id in ("commands", "presence"):
        job = scheduler.get_job(job_id)
        assert job is not None
        assert job.trigger.interval.total_seconds() == 30


@pytest.mark.usefixtures("clean_db")
async def test_commands_and_presence_jobs(
    engine: AsyncEngine, test_settings: Settings, factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    tenant = await factory.tenant()
    ctx = WorkerContext(settings=test_settings, engine=engine, sessionmaker=make_sessionmaker(engine))
    old = datetime.now(UTC) - timedelta(hours=3)
    async with ctx.sessionmaker() as s:
        agent = Agent(
            reseller_id=tenant.reseller_id,
            site_id=tenant.site_id,
            name="Velho",
            state="online",
            last_seen_at=old,
        )
        s.add(agent)
        await s.flush()
        s.add(
            Command(
                reseller_id=tenant.reseller_id, agent_id=agent.id, type="reconnect", params={}, expires_at=old
            )
        )
        s.add(
            AgentPresence(
                agent_id=agent.id,
                reseller_id=tenant.reseller_id,
                gateway_id="gw-morto",
                connected_at=old,
                last_seen_at=old,
            )
        )
        await s.commit()
    assert await commands_job(ctx) == 1
    assert await presence_job(ctx) == (1, 1)
    assert await commands_job(ctx) == 0
    assert await presence_job(ctx) == (0, 0)
    async with ctx.sessionmaker() as s:
        s.add(
            Device(
                reseller_id=tenant.reseller_id,
                site_id=tenant.site_id,
                customer_id=tenant.customer_id,
                serial="SEM-LEITURA",
                first_seen_at=old - timedelta(hours=10),
                last_read_at=old - timedelta(hours=10),
            )
        )
        await s.commit()
    assert await disconnected_job(ctx) == (1, 0)
    assert await disconnected_job(ctx) == (0, 0)
    messages = [r.getMessage() for r in caplog.records]
    assert any("1 comando(s) expirado(s)" in m for m in messages)
    assert any("presença(s) órfã(s)" in m for m in messages)
    assert any("marcado(s) como offline" in m for m in messages)
    assert any("marcado(s) como desconectado(s)" in m for m in messages)


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
