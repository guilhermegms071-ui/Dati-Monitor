"""Scheduled-jobs worker process. Run with: python -m app.worker.main"""

import asyncio
import logging
import signal
from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.core.db import DbStatus, check_database, make_engine
from app.core.logging import configure_logging

logger = logging.getLogger("app.worker")


@dataclass
class DbWatch:
    """Tracks database reachability so transitions are logged once, and failures always."""

    last_ok: bool | None = None


async def db_check_job(engine: AsyncEngine, watch: DbWatch) -> DbStatus:
    result = await check_database(engine)
    if not result.ok:
        logger.error("worker: banco indisponível: %s", result.error)
    elif watch.last_ok is not True:
        logger.info("worker: banco acessível (PostgreSQL %s)", result.server_version)
    watch.last_ok = result.ok
    return result


def build_scheduler(engine: AsyncEngine, settings: Settings, watch: DbWatch) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        db_check_job,
        "interval",
        seconds=settings.db_check_interval_seconds,
        args=[engine, watch],
        id="db_check",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


async def run(settings: Settings, stop: asyncio.Event) -> None:
    engine = make_engine(settings.database_url)
    watch = DbWatch()
    scheduler = build_scheduler(engine, settings, watch)
    await db_check_job(engine, watch)
    scheduler.start()
    logger.info("worker iniciado")
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        await engine.dispose()
        logger.info("worker encerrado")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = asyncio.Event()

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                # Windows event loops lack add_signal_handler; fall back to signal.signal.
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        await run(settings, stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
