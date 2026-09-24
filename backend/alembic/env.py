"""Alembic environment (async). The database URL comes from Settings.database_url."""

import asyncio
import re
from logging.config import fileConfig
from typing import Any

from sqlalchemy.engine import Connection

from alembic import context
from app.core.config import get_settings
from app.core.db import make_engine
from app.models import PARTITIONED_TABLES, Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Partições mensais (ex.: readings_2026_09, readings_default) são criadas em tempo de execução
# pela função dm_ensure_month_partition; não fazem parte do modelo.
_PARTITION_RE = re.compile(
    r"^(" + "|".join(re.escape(t) for t, _ in PARTITIONED_TABLES) + r")_(\d{4}_\d{2}|default)$"
)


def include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    if type_ == "table" and name and _PARTITION_RE.match(name):
        return False
    # O PostgreSQL clona para cada partição as FKs que apontam para tabelas particionadas.
    if type_ == "foreign_key_constraint" and reflected and _PARTITION_RE.match(obj.referred_table.name):
        return False
    return not (type_ == "index" and reflected and compare_to is None and name and _PARTITION_RE.match(name))


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url = config.attributes.get("database_url") or get_settings().database_url
    engine = make_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
