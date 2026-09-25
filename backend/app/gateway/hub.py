"""Connected agents of this gateway process and command delivery to them.

Delivery is triggered by PostgreSQL NOTIFY (instant), by every (re)connection and by a periodic sweep
(contingency for notifications lost while the listener was reconnecting). The database is the source of
truth: a command is marked `sent` in the same transaction that picks it, and one sent but never
confirmed is picked again after a few seconds — the agent ignores repeated ids.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas import agent as proto
from app.services import commands as commands_svc

logger = logging.getLogger(__name__)

# Códigos de fechamento do WebSocket (faixa 4000 a 4999 é da aplicação).
CLOSE_REPLACED = 4000  # o mesmo coletor abriu outra conexão
CLOSE_UNAUTHORIZED = 4401  # token inválido/expirado
CLOSE_REVOKED = 4403  # coletor revogado no portal
CLOSE_RATE_LIMITED = 4429
CLOSE_PROTOCOL = 4400
CLOSE_GOING_AWAY = 1001


@dataclass(eq=False)
class Connection:
    agent_id: uuid.UUID
    send_text: Callable[[str], Awaitable[None]]
    close: Callable[[int, str], Awaitable[None]]
    remote_addr: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, msg: proto.WsMessage) -> None:
        async with self.lock:
            await self.send_text(msg.model_dump_json())


def ws_message(kind: str, data: Mapping[str, object] | None = None) -> proto.WsMessage:
    return proto.WsMessage.model_validate({"type": kind, "data": dict(data or {})})


class Hub:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], gateway_id: str) -> None:
        self.sessionmaker = sessionmaker
        self.gateway_id = gateway_id
        self._conns: dict[uuid.UUID, Connection] = {}
        self._delivery_locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[object]] = set()

    def connected(self) -> list[uuid.UUID]:
        return list(self._conns)

    def get(self, agent_id: uuid.UUID) -> Connection | None:
        return self._conns.get(agent_id)

    async def register(self, conn: Connection) -> None:
        old = self._conns.get(conn.agent_id)
        self._conns[conn.agent_id] = conn
        if old is not None:
            logger.info("coletor %s abriu nova conexão; a anterior será fechada", conn.agent_id)
            await _safe_close(old, CLOSE_REPLACED, "replaced")

    def unregister(self, conn: Connection) -> bool:
        """Returns True when this was the agent's current connection."""
        if self._conns.get(conn.agent_id) is conn:
            del self._conns[conn.agent_id]
            return True
        return False

    def spawn(self, coro: Awaitable[object]) -> None:
        task: asyncio.Task[object] = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def deliver(self, agent_id: uuid.UUID) -> int:
        conn = self._conns.get(agent_id)
        if conn is None:
            return 0
        lock = self._delivery_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            async with self.sessionmaker() as session:
                cmds = await commands_svc.claim_for_delivery(session, agent_id)
                cancelled = await commands_svc.recently_cancelled(session, agent_id)
                await session.commit()
            sent = 0
            try:
                for cmd in cmds:
                    await conn.send(
                        ws_message("command", commands_svc.to_message(cmd).model_dump(mode="json"))
                    )
                    sent += 1
                for cid in cancelled:
                    await conn.send(ws_message("cancel", {"id": str(cid)}))
            except Exception as exc:  # noqa: BLE001 - conexão caiu no meio: fica "sent" e é reenviado
                logger.warning("falha ao entregar comandos ao coletor %s: %s", agent_id, exc)
            if sent:
                logger.info("%d comando(s) entregue(s) ao coletor %s", sent, agent_id)
            return sent

    async def sweep(self) -> None:
        for agent_id in self.connected():
            try:
                await self.deliver(agent_id)
            except Exception:
                logger.exception("varredura de comandos falhou para o coletor %s", agent_id)

    async def revoke(self, agent_id: uuid.UUID) -> None:
        conn = self._conns.get(agent_id)
        if conn is not None:
            logger.warning("coletor %s revogado no portal: conexão encerrada", agent_id)
            await _safe_close(conn, CLOSE_REVOKED, "agent_revoked")

    async def close_all(self) -> None:
        for conn in list(self._conns.values()):
            await _safe_close(conn, CLOSE_GOING_AWAY, "gateway_shutdown")
        for task in list(self._tasks):
            task.cancel()


async def _safe_close(conn: Connection, code: int, reason: str) -> None:
    try:
        await conn.close(code, reason)
    except Exception as exc:  # noqa: BLE001 - conexão já pode estar fechada
        logger.debug("fechar conexão do coletor %s: %s", conn.agent_id, exc)
