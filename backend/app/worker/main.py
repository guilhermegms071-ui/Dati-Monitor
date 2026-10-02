"""Scheduled-jobs worker process. Run with: python -m app.worker.main"""

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import DbStatus, check_database, make_engine, make_sessionmaker
from app.core.logging import configure_logging
from app.services import alert_engine
from app.services import cluster as cluster_svc
from app.services import commands as commands_svc
from app.services import erp_connector as erp_svc
from app.services import forecast as forecast_svc
from app.services import notifications as notif_svc
from app.services import park as park_svc
from app.services import presence as presence_svc
from app.services import retention as retention_svc
from app.services import updates as updates_svc
from app.services.alerts import emit_changes
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
    http: httpx.AsyncClient = field(default_factory=httpx.AsyncClient)


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
            stale, offline = await presence_svc.sweep(session, ctx.settings.agent_offline_after_seconds)
            await session.commit()
    except Exception:
        logger.exception("worker: falha na varredura de presença")
        raise
    if stale:
        logger.warning("worker: %d presença(s) órfã(s) removida(s) (gateway caiu sem limpar)", stale)
    if offline:
        logger.warning("worker: %d coletor(es) sem sinal marcado(s) como offline", offline)
    return stale, offline


async def disconnected_job(ctx: WorkerContext) -> tuple[int, int]:
    """Devices without a reading for DEVICE_DISCONNECTED_HOURS become "desconectado" (PROMPT 8)."""
    try:
        async with ctx.sessionmaker() as session:
            newly, back = await park_svc.mark_disconnected(session, ctx.settings.device_disconnected_hours)
            await session.commit()
    except Exception:
        logger.exception("worker: falha ao marcar equipamentos desconectados")
        raise
    if newly:
        logger.warning(
            "worker: %d equipamento(s) sem leitura há %d h marcado(s) como desconectado(s)",
            newly,
            ctx.settings.device_disconnected_hours,
        )
    if back:
        logger.info("worker: %d equipamento(s) voltaram a enviar leituras", back)
    return newly, back


async def cluster_job(ctx: WorkerContext) -> int:
    """MASTER lease (PROMPT 4.8): promotes a STANDBY when the MASTER's lease expired, or the operator's
    preferred MASTER once it is online."""
    try:
        async with ctx.sessionmaker() as session:
            changes = await cluster_svc.failover(session)
            await session.commit()
    except Exception:
        logger.exception("worker: falha no failover do cluster")
        raise
    return len(changes)


async def updates_job(ctx: WorkerContext) -> int:
    """Automatic signed updates by channel and gradual rollout (PROMPT 5.2)."""
    try:
        async with ctx.sessionmaker() as session:
            created = await updates_svc.offer_updates(session, ctx.settings)
            await session.commit()
    except Exception:
        logger.exception("worker: falha ao oferecer atualizações")
        raise
    if created:
        logger.info("worker: %d atualização(ões) automática(s) enviada(s)", created)
    return created


async def alerts_job(ctx: WorkerContext) -> alert_engine.EvalResult:
    """Rules → alerts (PROMPT 8, every minute): opens, de-duplicates and resolves automatically; then
    queues the notifications of every new alert (also those opened by the ingestion)."""
    try:
        async with ctx.sessionmaker() as session:
            result = await alert_engine.evaluate(session)
            queued = await notif_svc.enqueue_new_alerts(session, ctx.settings)
            if result.touched:
                await emit_changes(session, result.touched, result.opened, result.resolved)
            await session.commit()
    except Exception:
        logger.exception("worker: falha na avaliação de alertas")
        raise
    if result.opened or result.resolved:
        logger.info(
            "worker: %d alerta(s) aberto(s), %d resolvido(s) sozinho(s)", result.opened, result.resolved
        )
    if queued.notifications:
        logger.info("worker: %d notificação(ões) na fila", queued.notifications)
    return result


async def notify_job(ctx: WorkerContext) -> notif_svc.DeliveryResult:
    """Delivers due notifications with retry/backoff (PROMPT 9)."""
    try:
        async with ctx.sessionmaker() as session:
            result = await notif_svc.deliver_due(session, ctx.settings, ctx.http)
            await session.commit()
    except Exception:
        logger.exception("worker: falha no envio de notificações")
        raise
    if result.sent or result.failed:
        logger.info("worker: %d notificação(ões) enviada(s), %d falharam de vez", result.sent, result.failed)
    return result


async def erp_job(ctx: WorkerContext) -> tuple[int, int, int]:
    """Conector do Dataclassic (16.11): queues new alerts / daily counters and sends the due items."""
    try:
        async with ctx.sessionmaker() as session:
            now = datetime.now(UTC)
            queued = await erp_svc.enqueue(session, ctx.settings, now)
            await session.commit()
            sent, failed = await erp_svc.deliver_due(session, ctx.settings, ctx.http, now)
            await session.commit()
    except Exception:
        logger.exception("worker: falha no conector do ERP")
        raise
    if queued or sent or failed:
        logger.info("worker: ERP %d na fila, %d enviado(s), %d falha(s)", queued, sent, failed)
    return queued, sent, failed


async def forecast_job(ctx: WorkerContext) -> int:
    """Toner forecast (PROMPT 8/16.6): window, pages left, method and confidence."""
    try:
        async with ctx.sessionmaker() as session:
            done = await forecast_svc.run(session)
            await session.commit()
    except Exception:
        logger.exception("worker: falha na previsão de toner")
        raise
    return done


async def summary_job(ctx: WorkerContext) -> int:
    """Daily summary at 07:00 (São Paulo)."""
    try:
        async with ctx.sessionmaker() as session:
            created = await notif_svc.daily_summary(session, ctx.settings)
            await session.commit()
    except Exception:
        logger.exception("worker: falha no resumo diário")
        raise
    logger.info("worker: resumo diário enfileirado para %d destino(s)", created)
    return created


async def retention_job(ctx: WorkerContext) -> retention_svc.RetentionResult:
    """Retention (PROMPT 8): never deletes counter readings."""
    try:
        async with ctx.sessionmaker() as session:
            result = await retention_svc.run(session, ctx.settings)
            await session.commit()
    except Exception:
        logger.exception("worker: falha na retenção")
        raise
    logger.info(
        "worker: retenção apagou %d heartbeat(s), %d leitura(s) de suprimento e %d notificação(ões)",
        result.heartbeats,
        result.supply_readings,
        result.notifications,
    )
    return result


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
    scheduler.add_job(disconnected_job, "interval", minutes=5, args=[ctx], id="disconnected")
    scheduler.add_job(cluster_job, "interval", seconds=30, args=[ctx], id="cluster")
    scheduler.add_job(updates_job, "interval", minutes=5, args=[ctx], id="updates")
    scheduler.add_job(
        alerts_job, "interval", seconds=ctx.settings.alerts_interval_seconds, args=[ctx], id="alerts"
    )
    scheduler.add_job(
        notify_job, "interval", seconds=ctx.settings.notify_interval_seconds, args=[ctx], id="notify"
    )
    scheduler.add_job(erp_job, "interval", seconds=ctx.settings.erp_interval_seconds, args=[ctx], id="erp")
    scheduler.add_job(
        forecast_job,
        "interval",
        minutes=ctx.settings.forecast_interval_minutes,
        args=[ctx],
        id="forecast",
        next_run_time=datetime.now(UTC),  # também na partida: não esperar 1 h pela primeira previsão
    )
    scheduler.add_job(
        summary_job, "cron", hour=7, minute=0, timezone="America/Sao_Paulo", args=[ctx], id="daily_summary"
    )
    scheduler.add_job(retention_job, "cron", hour=3, minute=40, args=[ctx], id="retention")
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
        await ctx.http.aclose()
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
