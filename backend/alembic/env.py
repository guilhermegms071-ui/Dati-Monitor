"""Alembic environment (async). The database URL comes from Settings.database_url."""

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection

from alembic import context
from app.core.config import get_settings
from app.core.db import make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Os modelos ORM entram na Fase 1; até lá não há metadata para autogenerate.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = make_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
