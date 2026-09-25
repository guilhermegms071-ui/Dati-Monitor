"""Dedicated PostgreSQL connection that LISTENs for commands and revocations (no Redis).

If the connection drops (database restart, network), it reconnects with backoff and runs a full sweep,
so notifications sent while it was down are not lost.
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Callable
from typing import Any

import asyncpg
from sqlalchemy.engine import make_url

from app.core.notify import CH_AGENT_REVOKED, CH_COMMAND
from app.gateway.hub import Hub

logger = logging.getLogger(__name__)


def asyncpg_dsn(database_url: str) -> str:
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def _setter(event: asyncio.Event) -> Callable[[object], None]:
    def on_terminated(_conn: object) -> None:
        event.set()

    return on_terminated


class Listener:
    def __init__(self, database_url: str, hub: Hub) -> None:
        self.dsn = asyncpg_dsn(database_url)
        self.hub = hub
        self.connected = asyncio.Event()

    def _on_notify(self, _conn: Any, _pid: int, channel: str, payload: str) -> None:
        try:
            agent_id = uuid.UUID(payload)
        except ValueError:
            logger.error("notificação com payload inválido em %s: %r", channel, payload)
            return
        if channel == CH_COMMAND:
            self.hub.spawn(self.hub.deliver(agent_id))
        elif channel == CH_AGENT_REVOKED:
            self.hub.spawn(self.hub.revoke(agent_id))

    async def run(self) -> None:
        delay = 1.0
        while True:
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(self.dsn)
                lost = asyncio.Event()
                conn.add_termination_listener(_setter(lost))
                await conn.add_listener(CH_COMMAND, self._on_notify)
                await conn.add_listener(CH_AGENT_REVOKED, self._on_notify)
                self.connected.set()
                logger.info(
                    "gateway escutando comandos no PostgreSQL (LISTEN %s, %s)", CH_COMMAND, CH_AGENT_REVOKED
                )
                delay = 1.0
                await self.hub.sweep()  # o que chegou enquanto estava desconectado
                await lost.wait()
                logger.error("conexão LISTEN com o PostgreSQL caiu; reconectando")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconecta para sempre, com o erro no log
                logger.error("LISTEN no PostgreSQL falhou (%s); nova tentativa em %.0f s", exc, delay)
            finally:
                self.connected.clear()
                if conn is not None and not conn.is_closed():
                    await conn.close()
            await asyncio.sleep(delay * (0.8 + random.random() * 0.4))  # noqa: S311 - jitter
            delay = min(delay * 2, 60.0)
