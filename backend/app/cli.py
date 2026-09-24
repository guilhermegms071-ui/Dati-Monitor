"""Administrative commands. Usage: python -m app.cli <migrate|bootstrap|seed-dev|ensure-partitions>"""

import argparse
import asyncio
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.core.config import get_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.logging import configure_logging
from app.services.bootstrap import announce_bootstrap, ensure_bootstrap, seed_dev
from app.services.partitions import ensure_partitions

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    if database_url:
        cfg.attributes["database_url"] = database_url
    return cfg


def migrate(database_url: str | None = None) -> None:
    command.upgrade(alembic_config(database_url), "head")


async def _bootstrap() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            result = await ensure_bootstrap(session, settings)
            await session.commit()
        announce_bootstrap(result)
        if not result.created_admin:
            print("Bootstrap: usuários já existem; nada a fazer.")  # noqa: T201
    finally:
        await engine.dispose()


async def _seed_dev() -> None:
    settings = get_settings()
    if settings.app_env == "production":
        raise SystemExit("seed-dev é só para desenvolvimento (APP_ENV=production)")
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            boot = await ensure_bootstrap(session, settings)
            result = await seed_dev(session)
            await session.commit()
        announce_bootstrap(boot)
        print("Seed de desenvolvimento:", "criado" if result.created else "já existia")  # noqa: T201
    finally:
        await engine.dispose()


async def _ensure_partitions() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            created = await ensure_partitions(session)
            await session.commit()
        print("Partições criadas:", ", ".join(created) if created else "nenhuma (já existiam)")  # noqa: T201
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="Comandos administrativos do backend"
    )
    parser.add_argument("command", choices=["migrate", "bootstrap", "seed-dev", "ensure-partitions"])
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    match args.command:
        case "migrate":
            migrate()
        case "bootstrap":
            asyncio.run(_bootstrap())
        case "seed-dev":
            asyncio.run(_seed_dev())
        case "ensure-partitions":
            asyncio.run(_ensure_partitions())
    return 0


if __name__ == "__main__":
    sys.exit(main())
