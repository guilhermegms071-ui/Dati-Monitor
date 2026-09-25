"""Scheduled-jobs worker process. Run with: python -m app.worker.main"""

import asyncio
import logging
import signal
from dataclasses import dataclass, field

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import DbStatus, check_database, make_engine, make_sessionmaker
from app.core.logging import configure_logging
from app.services import commands as commands_svc
from app.services import presence as presence_svc
from app.services.partitions import default_partition_rows, ensure_partitions

logger = logging.getLogger("app.worker")


@dataclass
class DbWatch:
    """Tracks database reachability so transitions are logged once, and failures always."""

    last_ok: bool | None = None


@dataclass
class WorkerContext:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    db_watch: DbWatch = field(default_factory=DbWatch)


async def db_check_job(engine: AsyncEngine, watch: DbWatch) -> DbStatus:
    result = await check_database(engine)
    if not result.ok:
        logger.error("worker: banco indisponível: %s", result.error)
    elif watch.last_ok is not True:
        logger.info("worker: banco acessível (PostgreSQL %s)", result.server_version)
    watch.last_ok = result.ok
    return result


async def partitions_job(ctx: WorkerContext) -> list[str]:
    """Keeps monthly partitions 1 month back and 3 ahead; warns if rows landed in a default partition."""
    try:
        async with ctx.sessionmaker() as session:
            created = await ensure_partitions(session)
            leftovers = await default_partition_rows(session)
            await session.commit()
    except Exception:
        logger.exception("worker: falha na manutenção de partições")
        raise
    for table, count in leftovers.items():
        if count:
            logger.warning(
                "worker: %d linha(s) em %s_default (data fora da janela de partições)", count, table
            )
    return created


async def commands_job(ctx: WorkerContext) -> int:
    """Expires commands nobody picked up in time (PROMPT 4.7: default 10 min)."""
    try:
        async with ctx.sessionmaker() as session:
            changed = await commands_svc.expire_commands(session)
            await session.commit()
    except Exception:
        logger.exception("worker: falha ao expirar comandos")
        raise
    if changed:
        logger.info("worker: %d comando(s) expirado(s)", changed)
    return changed


async def presence_job(ctx: WorkerContext) -> tuple[int, int]:
    """Removes presence of dead gateways and marks agents without heartbeat as offline."""
    try:
        async with ctx.sessionmaker() as session:
            stale, offline = await presence_svc.sweep(session)
            await session.commit()
    except Exception:
        logger.exception("worker: falha na varredura de presença")
        raise
    if stale:
        logger.warning("worker: %d presença(s) órfã(s) removida(s) (gateway caiu sem limpar)", stale)
    if offline:
        logger.warning("worker: %d coletor(es) sem sinal marcado(s) como offline", offline)
    return stale, offline


def build_scheduler(ctx: WorkerContext) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC", job_defaults={"max_instances": 1, "coalesce": True})
    scheduler.add_job(
        db_check_job,
        "interval",
        seconds=ctx.settings.db_check_interval_seconds,
        args=[ctx.engine, ctx.db_watch],
        id="db_check",
    )
    scheduler.add_job(partitions_job, "cron", hour=3, minute=15, args=[ctx], id="partitions")
    scheduler.add_job(commands_job, "interval", seconds=30, args=[ctx], id="commands")
    scheduler.add_job(presence_job, "interval", seconds=30, args=[ctx], id="presence")
    return scheduler


async def run(settings: Settings, stop: asyncio.Event) -> None:
    engine = make_engine(settings.database_url, pool_size=5)
    ctx = WorkerContext(settings=settings, engine=engine, sessionmaker=make_sessionmaker(engine))
    scheduler = build_scheduler(ctx)
    await db_check_job(engine, ctx.db_watch)
    if ctx.db_watch.last_ok:
        try:
            await partitions_job(ctx)
        except Exception:  # noqa: BLE001 - já registrado; o job agendado tentará de novo
            logger.error("worker: manutenção inicial de partições falhou; nova tentativa no horário agendado")
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
