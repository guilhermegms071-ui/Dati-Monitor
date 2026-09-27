"""Agent WebSocket gateway process (port 8001 in development; /ws/agent behind Caddy in production).

One persistent connection per agent (PROMPT 4.3): heartbeats every 30 s, real-time commands and their
progress. Commands arrive through PostgreSQL LISTEN/NOTIFY; presence is kept in `agent_presence`.
"""

import asyncio
import logging
import os
import secrets
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.errors import AppError
from app.core.health import health_router
from app.core.logging import configure_logging
from app.core.product import get_product
from app.core.responses import UTF8JSONResponse
from app.core.version import backend_version
from app.gateway.hub import (
    CLOSE_PROTOCOL,
    CLOSE_RATE_LIMITED,
    CLOSE_REVOKED,
    CLOSE_UNAUTHORIZED,
    Connection,
    Hub,
    ws_message,
)
from app.gateway.listener import Listener
from app.models import Agent
from app.schemas import agent as proto
from app.services import agents as agents_svc
from app.services import commands as commands_svc
from app.services import presence as presence_svc

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30
# Limite por conexão: um coletor normal manda ~1 mensagem a cada poucos segundos.
RATE_WINDOW_SECONDS = 10.0
RATE_MAX_MESSAGES = 100
MAX_PROTOCOL_ERRORS = 10


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    gateway_id = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        sessionmaker = make_sessionmaker(engine)
        hub = Hub(sessionmaker, gateway_id)
        listener = Listener(settings.database_url, hub)
        app.state.engine, app.state.hub, app.state.listener = engine, hub, listener

        async def periodic_sweep() -> None:
            while True:
                await asyncio.sleep(settings.gateway_sweep_seconds)
                await hub.sweep()

        tasks = [asyncio.create_task(listener.run()), asyncio.create_task(periodic_sweep())]
        logger.info("gateway %s iniciado", gateway_id)
        try:
            yield
        finally:
            await hub.close_all()
            for t in tasks:
                t.cancel()
            for t in tasks:
                with suppress(asyncio.CancelledError):
                    await t
            async with sessionmaker() as session:
                for agent_id in hub.connected():
                    await presence_svc.disconnect(session, agent_id, gateway_id)
                await session.commit()
            await engine.dispose()

    app = FastAPI(
        title=f"{get_product().name} Gateway",
        version=backend_version(),
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    app.include_router(health_router("/health", "gateway", _engine))

    @app.websocket("/ws/agent")
    async def ws_agent(ws: WebSocket) -> None:
        await accept_agent(ws, settings)

    return app


async def accept_agent(ws: WebSocket, settings: Settings) -> None:
    """Handshake: authenticates the Bearer token, records presence and starts the message loop."""
    hub: Hub = ws.app.state.hub
    await ws.accept()
    remote = ws.client.host if ws.client else None
    async with hub.sessionmaker() as session:
        try:
            agent = await agents_svc.authenticate(session, settings, ws.headers.get("authorization", ""))
        except AppError as exc:
            code = CLOSE_REVOKED if exc.code == "agent_revoked" else CLOSE_UNAUTHORIZED
            logger.warning("conexão de coletor recusada (%s) de %s", exc.code, remote)
            await ws.close(code=code, reason=exc.code)
            return
        await presence_svc.connect(session, agent, hub.gateway_id, remote)
        await agents_svc.emit_state(session, agent)  # portal ao vivo: coletor conectado
        await session.commit()
        agent_id = agent.id

    async def close(code: int, reason: str) -> None:
        await ws.close(code=code, reason=reason)

    conn = Connection(agent_id, ws.send_text, close, remote)
    await hub.register(conn)
    logger.info("coletor %s conectado pelo WebSocket (%s)", agent_id, remote)
    session_ = AgentSession(ws, hub, conn)
    try:
        await conn.send(
            ws_message(
                "welcome",
                proto.Welcome(
                    agent_id=str(agent_id), server_time=datetime.now(UTC), heartbeat_seconds=HEARTBEAT_SECONDS
                ).model_dump(mode="json"),
            )
        )
        await hub.deliver(agent_id)
        await session_.loop()
    except WebSocketDisconnect as exc:
        logger.info("coletor %s desconectou (código %s)", agent_id, exc.code)
    finally:
        if hub.unregister(conn):
            try:
                async with hub.sessionmaker() as session:
                    await presence_svc.disconnect(session, agent_id, hub.gateway_id)
                    gone = await session.get(Agent, agent_id)
                    if gone is not None:
                        await agents_svc.emit_state(session, gone)
                    await session.commit()
            except Exception:
                logger.exception("não foi possível apagar a presença do coletor %s", agent_id)


class AgentSession:
    """Message loop of one authenticated agent connection (see proto.WsMessage)."""

    def __init__(self, ws: WebSocket, hub: Hub, conn: Connection) -> None:
        self.ws = ws
        self.hub = hub
        self.conn = conn
        self.agent_id = conn.agent_id
        self.window_start = time.monotonic()
        self.window_count = 0
        self.protocol_errors = 0

    def _rate_ok(self) -> bool:
        now = time.monotonic()
        if now - self.window_start > RATE_WINDOW_SECONDS:
            self.window_start, self.window_count = now, 0
        self.window_count += 1
        return self.window_count <= RATE_MAX_MESSAGES

    async def _error(self, code: str, message: str, ref: str | None = None) -> None:
        self.protocol_errors += 1
        data: dict[str, object] = {"code": code, "message": message}
        if ref:
            data["id"] = ref
        await self.conn.send(ws_message("error", data))
        if self.protocol_errors > MAX_PROTOCOL_ERRORS:
            await self.ws.close(code=CLOSE_PROTOCOL, reason="too_many_errors")
            raise WebSocketDisconnect(CLOSE_PROTOCOL)

    async def loop(self) -> None:
        while True:
            raw = await self.ws.receive_text()
            if not self._rate_ok():
                logger.warning("coletor %s excedeu o limite de mensagens; conexão encerrada", self.agent_id)
                await self.ws.close(code=CLOSE_RATE_LIMITED, reason="rate_limited")
                return
            try:
                msg = proto.WsMessage.model_validate_json(raw)
            except ValidationError:
                await self._error("invalid_message", "Mensagem inválida (esperado JSON com v=1 e type)")
                continue
            match msg.type:
                case "heartbeat":
                    await self._heartbeat(msg.data)
                case "command_update":
                    await self._command_update(msg.data)
                case "hello":
                    try:
                        hello = proto.Hello.model_validate(msg.data)
                    except ValidationError:
                        await self._error("invalid_hello", "Mensagem hello inválida")
                        continue
                    logger.info("coletor %s versão %s", self.agent_id, hello.version or "?")
                case _:
                    await self._error("unexpected_type", f"Tipo não esperado do coletor: {msg.type}")

    async def _heartbeat(self, data: dict[str, object]) -> None:
        try:
            req = proto.HeartbeatRequest.model_validate(data)
        except ValidationError:
            await self._error("invalid_heartbeat", "Heartbeat inválido")
            return
        async with self.hub.sessionmaker() as session:
            agent = await session.get(Agent, self.agent_id)
            if agent is None or agent.deleted_at is not None or agent.revoked_at is not None:
                await self.ws.close(code=CLOSE_REVOKED, reason="agent_revoked")
                raise WebSocketDisconnect(CLOSE_REVOKED)
            resp = await agents_svc.heartbeat(session, agent, req, channel="ws")
            await presence_svc.touch(session, agent.id, self.hub.gateway_id, req.latency_ms)
            await session.commit()
        await self.conn.send(ws_message("heartbeat_ack", resp.model_dump(mode="json")))

    async def _command_update(self, data: dict[str, object]) -> None:
        try:
            upd = proto.CommandUpdate.model_validate(data)
        except ValidationError:
            await self._error("invalid_command_update", "Atualização de comando inválida")
            return
        async with self.hub.sessionmaker() as session:
            agent = await session.get(Agent, self.agent_id)
            if agent is None:
                return
            try:
                cmd = await commands_svc.apply_update(session, agent, upd)
            except AppError as exc:
                await self._error(exc.code, exc.message, upd.id)
                return
            await presence_svc.touch(session, agent.id, self.hub.gateway_id)
            await session.commit()
            ack = {"id": str(cmd.id), "state": cmd.state}
        await self.conn.send(ws_message("command_update_ack", ack))
