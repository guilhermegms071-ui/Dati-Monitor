"""Acesso remoto à página web da impressora (PROMPT 4.9).

The portal opens a session for a device (or for an IP typed by the technician, accepted only when it is a
device registered in that site). The API picks a collector of the site connected to the gateway (the
MASTER first), sends it `web_proxy_open` and returns `/devweb/{token}/?dm_key=...`. The first browser that
uses the link gets a cookie bound to the session; the link does not work in another browser. Everything
is audited, including refused attempts.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, bad_request, conflict, forbidden, not_found
from app.core.principal import Principal
from app.models import Agent, AgentPresence, Device, Site, User, WebSession
from app.models.agents import PRESENCE_STALE_SECONDS
from app.schemas.agent import WEB_PORTS
from app.schemas.web_access import WebSessionOut
from app.services import audit, tenancy
from app.services import commands as commands_svc
from app.services import devices as devices_svc

DEVWEB_PREFIX = "/devweb"
KEY_PARAM = "dm_key"
COOKIE = "dm_devweb"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def path_prefix(token: str) -> str:
    return f"{DEVWEB_PREFIX}/{token}/"


async def _deny(
    session: AsyncSession, p: Principal, *, reason: str, site: Site, ip: str, port: int, message: str
) -> AppError:
    """Refused attempt: audited (and committed by the caller before raising) — criterion 14."""
    await audit.record(
        session,
        p,
        action="web_session.denied",
        entity="site",
        entity_id=site.id,
        reseller_id=site.reseller_id,
        after={"ip": ip, "port": port, "reason": reason},
    )
    await session.commit()
    return forbidden(message)


async def _pick_agent(session: AsyncSession, site: Site) -> Agent:
    """A collector of the site with a live WebSocket (the tunnel runs inside it); the MASTER first."""
    fresh = datetime.now(UTC) - timedelta(seconds=PRESENCE_STALE_SECONDS)
    rows = (
        await session.execute(
            select(Agent)
            .join(AgentPresence, AgentPresence.agent_id == Agent.id)
            .where(
                Agent.site_id == site.id,
                Agent.deleted_at.is_(None),
                Agent.revoked_at.is_(None),
                AgentPresence.last_seen_at >= fresh,
            )
        )
    ).scalars()
    agents = sorted(rows, key=lambda a: (a.id != site.master_agent_id, a.cluster_role != "master", a.name))
    if not agents:
        raise conflict(
            "no_agent_online",
            "Nenhum coletor deste local está conectado agora; a página web precisa de um coletor online",
        )
    return agents[0]


@dataclass(frozen=True)
class _Target:
    device: Device
    site: Site


async def _open(
    session: AsyncSession, settings: Settings, p: Principal, t: _Target, *, port: int, scheme: str
) -> WebSessionOut:
    agent = await _pick_agent(session, t.site)
    token = secrets.token_urlsafe(32)
    key = secrets.token_urlsafe(24)
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.web_session_minutes)
    ws = WebSession(
        reseller_id=t.device.reseller_id,
        user_id=p.user_id,
        device_id=t.device.id,
        agent_id=agent.id,
        ip=t.device.ip or "",
        port=port,
        scheme=scheme,
        token_hash=_hash(token),
        key_hash=_hash(key),
        expires_at=expires,
    )
    session.add(ws)
    await session.flush()
    cmd = await commands_svc.insert_command(
        session,
        p,
        agent,
        "web_proxy_open",
        {
            "session_id": str(ws.id),
            "ip": ws.ip,
            "port": port,
            "scheme": scheme,
            "expires_at": expires.isoformat(),
            "max_bytes_per_second": settings.web_max_bytes_per_second,
        },
        target="agent",
        expires_in=timedelta(minutes=2),
    )
    ws.command_id = cmd.id
    await audit.record(
        session,
        p,
        action="web_session.open",
        entity="device",
        entity_id=t.device.id,
        reseller_id=t.device.reseller_id,
        after={
            "session_id": str(ws.id),
            "ip": ws.ip,
            "port": port,
            "scheme": scheme,
            "agent_id": str(agent.id),
        },
    )
    await session.flush()
    return WebSessionOut(
        id=ws.id,
        url=f"{path_prefix(token)}?{KEY_PARAM}={key}",
        device_id=t.device.id,
        ip=ws.ip,
        port=port,
        scheme=scheme,
        agent_name=agent.name,
        expires_at=expires,
    )


def _check_port(port: int, scheme: str) -> None:
    if port not in WEB_PORTS:
        raise bad_request(
            "web_port_not_allowed", "Portas permitidas: " + ", ".join(str(x) for x in WEB_PORTS)
        )
    if scheme not in ("http", "https"):
        raise bad_request("web_scheme_invalid", "Use http ou https")


async def open_for_device(
    session: AsyncSession, settings: Settings, p: Principal, device_id: uuid.UUID, *, port: int, scheme: str
) -> WebSessionOut:
    p.require("devices.web_access")
    _check_port(port, scheme)
    device = await devices_svc.get_device(session, p, device_id)
    if device.discovery_state != "approved" or not device.active:
        raise conflict("device_not_active", "Só equipamentos ativos no parque podem ser abertos")
    if not device.ip:
        raise conflict("device_without_ip", "O equipamento não tem IP conhecido")
    site = await session.get(Site, device.site_id)
    if site is None:
        raise not_found("Local")
    return await _open(session, settings, p, _Target(device, site), port=port, scheme=scheme)


async def open_by_ip(
    session: AsyncSession,
    settings: Settings,
    p: Principal,
    site_id: uuid.UUID,
    *,
    ip: str,
    port: int,
    scheme: str,
) -> WebSessionOut:
    """The technician typed an IP: only a device registered (and active) in this site is accepted."""
    p.require("devices.web_access")
    site = await tenancy.get_site(session, p, site_id)
    if port not in WEB_PORTS:
        raise await _deny(
            session,
            p,
            reason="port_not_allowed",
            site=site,
            ip=ip,
            port=port,
            message="Porta não permitida: " + ", ".join(str(x) for x in WEB_PORTS),
        )
    device = (
        await session.execute(
            select(Device).where(
                Device.site_id == site.id,
                Device.ip == ip.strip(),
                Device.deleted_at.is_(None),
                Device.active.is_(True),
                Device.discovery_state == "approved",
            )
        )
    ).scalar_one_or_none()
    if device is None:
        raise await _deny(
            session,
            p,
            reason="ip_not_registered",
            site=site,
            ip=ip,
            port=port,
            message="Este IP não é de uma impressora cadastrada neste local: acesso recusado e registrado",
        )
    return await _open(session, settings, p, _Target(device, site), port=port, scheme=scheme)


async def close(session: AsyncSession, p: Principal, session_id: uuid.UUID) -> None:
    ws = await session.get(WebSession, session_id)
    if ws is None or not p.can_access_reseller(ws.reseller_id):
        raise not_found("Sessão")
    if ws.user_id != p.user_id:
        p.require("audit.read")  # admin pode encerrar a sessão de outro usuário
    if ws.closed_at is None:
        ws.closed_at = datetime.now(UTC)
        await audit.record(
            session,
            p,
            action="web_session.close",
            entity="device",
            entity_id=ws.device_id,
            reseller_id=ws.reseller_id,
            after={"session_id": str(ws.id), "requests": ws.requests, "bytes_out": ws.bytes_out},
        )


# ----------------------------------------------------------------------------- gateway


class SessionRefused(Exception):  # noqa: N818 - nome do domínio
    """The /devweb request cannot go on; the message is shown to the user."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class ActiveSession:
    id: uuid.UUID
    agent_id: uuid.UUID
    ip: str
    port: int
    scheme: str
    expires_at: datetime
    bytes_out: int
    user_name: str
    command_state: str | None


async def resolve(session: AsyncSession, token: str) -> tuple[WebSession, ActiveSession]:
    row = (
        (
            await session.execute(
                select(WebSession, User.name)
                .join(User, User.id == WebSession.user_id)
                .where(WebSession.token_hash == _hash(token))
            )
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        raise SessionRefused(404, "Sessão de acesso web inexistente")
    ws, user_name = row
    now = datetime.now(UTC)
    if ws.closed_at is not None:
        raise SessionRefused(410, "Sessão encerrada; abra de novo pelo portal")
    if ws.expires_at <= now:
        raise SessionRefused(410, "Sessão expirada (30 min); abra de novo pelo portal")
    cmd_state = None
    if ws.command_id is not None:
        from app.models import Command  # noqa: PLC0415 - evita ciclo de importação

        cmd = await session.get(Command, ws.command_id)
        cmd_state = cmd.state if cmd else None
    return ws, ActiveSession(
        ws.id, ws.agent_id, ws.ip, ws.port, ws.scheme, ws.expires_at, ws.bytes_out, user_name, cmd_state
    )


def check_key(ws: WebSession, key: str) -> bool:
    return secrets.compare_digest(ws.key_hash, _hash(key))


async def bind(session: AsyncSession, ws: WebSession, key: str) -> None:
    """First use of the link: the session belongs to this browser from now on."""
    if ws.bound_at is not None:
        raise SessionRefused(
            403, "Este link já foi aberto em outro navegador; abra uma nova sessão pelo portal"
        )
    if not check_key(ws, key):
        raise SessionRefused(403, "Link de acesso inválido")
    ws.bound_at = datetime.now(UTC)
    await session.commit()


async def account(session: AsyncSession, session_id: uuid.UUID, nbytes: int) -> None:
    await session.execute(
        update(WebSession)
        .where(WebSession.id == session_id)
        .values(
            requests=WebSession.requests + 1,
            bytes_out=WebSession.bytes_out + nbytes,
            last_used_at=datetime.now(UTC),
        )
    )
    await session.commit()
