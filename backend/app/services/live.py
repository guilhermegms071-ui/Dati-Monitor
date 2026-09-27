"""Live updates for the portal (PROMPT 7: SSE for collector state, command progress and new data).

One dedicated PostgreSQL connection per API process LISTENs to the live channels and fans each event
out to the connected portals whose scope allows it (reseller, and customer for customer users). Events
carry only ids; the portal refetches what changed.
"""

import asyncio
import contextlib
import json
import logging
import random
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from sqlalchemy.engine import make_url

from app.core.notify import LIVE_CHANNELS
from app.core.principal import Principal

logger = logging.getLogger(__name__)

QUEUE_SIZE = 500
KEEPALIVE_SECONDS = 15.0


@dataclass(eq=False)
class Subscriber:
    principal: Principal
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    overflowed: bool = False

    def allows(self, event: dict[str, Any]) -> bool:
        p = self.principal
        if not p.is_superadmin and event.get("reseller_id") != str(p.reseller_id):
            return False
        return p.customer_id is None or event.get("customer_id") == str(p.customer_id)


class LiveBroker:
    def __init__(self, database_url: str) -> None:
        self.dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
        self.subscribers: set[Subscriber] = set()
        self.connected = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def subscribe(self, principal: Principal) -> Subscriber:
        sub = Subscriber(principal)
        self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self.subscribers.discard(sub)

    def publish(self, event: dict[str, Any]) -> None:
        for sub in list(self.subscribers):
            if not sub.allows(event):
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Portal lento: descarta e pede para ele recarregar tudo (evento "resync").
                sub.overflowed = True

    def _on_notify(self, _conn: Any, _pid: int, channel: str, payload: str) -> None:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            logger.error("evento ao vivo inválido em %s: %r", channel, payload[:200])
            return
        if isinstance(event, dict):
            self.publish(event)

    async def _run(self) -> None:
        delay = 1.0
        while True:
            conn = None
            try:
                conn = await asyncpg.connect(self.dsn)
                lost = asyncio.Event()
                conn.add_termination_listener(lambda _c, ev=lost: ev.set())
                for ch in LIVE_CHANNELS:
                    await conn.add_listener(ch, self._on_notify)
                self.connected.set()
                delay = 1.0
                # Reconectou: portais podem ter perdido eventos — pedem recarga.
                self.publish_resync()
                await lost.wait()
                logger.error("conexão de eventos ao vivo com o PostgreSQL caiu; reconectando")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconecta para sempre, com o erro no log
                logger.error("LISTEN de eventos ao vivo falhou (%s); nova tentativa em %.0f s", exc, delay)
            finally:
                self.connected.clear()
                if conn is not None and not conn.is_closed():
                    await conn.close()
            await asyncio.sleep(delay * (0.8 + random.random() * 0.4))  # noqa: S311 - jitter
            delay = min(delay * 2, 60.0)

    def publish_resync(self) -> None:
        for sub in list(self.subscribers):
            sub.overflowed = True


async def stream(broker: LiveBroker, principal: Principal, is_disconnected: Any) -> AsyncIterator[str]:
    """Server-Sent Events: `event: <type>` + JSON data; comment keep-alives every 15 s."""
    sub = broker.subscribe(principal)
    try:
        yield "retry: 5000\n\n"
        yield f"event: hello\ndata: {json.dumps({'session': str(uuid.uuid4())})}\n\n"
        while True:
            if sub.overflowed:
                sub.overflowed = False
                while not sub.queue.empty():
                    sub.queue.get_nowait()
                yield "event: resync\ndata: {}\n\n"
            try:
                event = await asyncio.wait_for(sub.queue.get(), KEEPALIVE_SECONDS)
            except TimeoutError:
                if await is_disconnected():
                    return
                yield ": keep-alive\n\n"
                continue
            kind = str(event.get("type", "message"))
            yield f"event: {kind}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
    finally:
        broker.unsubscribe(sub)
