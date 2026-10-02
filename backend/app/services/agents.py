"""Collectors: portal management, enrollment (code → secret), HMAC session tokens, heartbeats,
configuration delivery and range suggestions (PROMPT 4.2/4.3/4.5/4.8)."""

import base64
import hmac
import logging
import secrets
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import Settings
from app.core.errors import AppError, bad_request, conflict, not_found, unauthorized
from app.core.notify import CH_AGENT_REVOKED, CH_AGENT_STATE, notify, notify_event
from app.core.principal import Principal, customer_scope, reseller_scope
from app.core.security import (
    InvalidTokenError,
    agent_signature,
    create_agent_token,
    decode_agent_token,
    derive_agent_key,
)
from app.models import (
    Agent,
    AgentEnrollmentCode,
    AgentHeartbeat,
    ClusterEvent,
    Customer,
    Device,
    DeviceEvent,
    IpRange,
    ReadingIdempotency,
    ReadProfile,
    Site,
    SnmpCredential,
)
from app.schemas import agent as proto
from app.schemas.collection import DEFAULT_COLLECTION, AgentIn, AgentStats, AgentUpdate
from app.services import audit
from app.services.pagination import Direction, PageResult, SortOption, paginate

logger = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O e 1/I
CODE_VALIDITY = timedelta(days=7)
CLOCK_SKEW_LIMIT = 300  # segundos
LEASE_DURATION = timedelta(minutes=3)
DEGRADED_QUEUE = 1000
MAX_SNMP_RETRIES = 4  # tentativas de 1 a 5 (seção 16.10)


def _now() -> datetime:
    return datetime.now(UTC)


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def _aad(agent_id: uuid.UUID) -> bytes:
    return b"agent-key:" + agent_id.bytes


# ----------------------------------------------------------------------------- portal


AGENT_SORTS = {
    "name": SortOption(Agent.name, "str"),
    "last_seen_at": SortOption(
        func.coalesce(Agent.last_seen_at, datetime(1970, 1, 1, tzinfo=UTC)), "datetime"
    ),
    "created_at": SortOption(Agent.created_at, "datetime"),
}


async def list_agents(
    session: AsyncSession,
    p: Principal,
    *,
    site_id: uuid.UUID | None,
    customer_id: uuid.UUID | None,
    state: str | None,
    q: str | None,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> PageResult[Agent]:
    p.require("agents.read")
    stmt = (
        select(Agent)
        .join(Site, Site.id == Agent.site_id)
        .where(
            Agent.deleted_at.is_(None),
            reseller_scope(p, Agent.reseller_id),
            customer_scope(p, Site.customer_id),
        )
    )
    if site_id:
        stmt = stmt.where(Agent.site_id == site_id)
    if customer_id:
        stmt = stmt.where(Site.customer_id == customer_id)
    if state:
        stmt = stmt.where(Agent.state == state)
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(Agent.name.ilike(like), Agent.hostname.ilike(like)))
    return await paginate(
        session,
        stmt,
        id_column=Agent.id,
        sort_options=AGENT_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )


async def get_agent(session: AsyncSession, p: Principal, agent_id: uuid.UUID) -> Agent:
    p.require("agents.read")
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.deleted_at is not None or not p.can_access_reseller(agent.reseller_id):
        raise not_found("Coletor")
    if p.customer_id is not None:
        site = await session.get(Site, agent.site_id)
        if site is None or site.customer_id != p.customer_id:
            raise not_found("Coletor")
    return agent


async def site_names(
    session: AsyncSession, site_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, uuid.UUID, str]]:
    """site_id → (nome do local, id do cliente, nome do cliente), numa consulta só."""
    if not site_ids:
        return {}
    rows = await session.execute(
        select(Site.id, Site.name, Customer.id, Customer.name)
        .join(Customer, Customer.id == Site.customer_id)
        .where(Site.id.in_(site_ids))
    )
    return {sid: (sname, cid, cname) for sid, sname, cid, cname in rows.tuples().all()}


async def site_in_scope(session: AsyncSession, p: Principal, site_id: uuid.UUID) -> Site:
    site = await session.get(Site, site_id)
    if (
        site is None
        or site.deleted_at is not None
        or not p.can_access_customer(site.reseller_id, site.customer_id)
    ):
        raise not_found("Local")
    return site


async def _issue_code(session: AsyncSession, p: Principal, agent: Agent) -> AgentEnrollmentCode:
    now = _now()
    await session.execute(
        update(AgentEnrollmentCode)
        .where(AgentEnrollmentCode.agent_id == agent.id, AgentEnrollmentCode.used_at.is_(None))
        .values(used_at=now)
    )
    for _ in range(10):
        code = new_code()
        exists = (
            await session.execute(
                select(func.count()).select_from(AgentEnrollmentCode).where(AgentEnrollmentCode.code == code)
            )
        ).scalar_one()
        if not exists:
            break
    else:
        raise AppError(500, "code_generation_failed", "Não foi possível gerar um código único")
    row = AgentEnrollmentCode(
        code=code,
        reseller_id=agent.reseller_id,
        site_id=agent.site_id,
        agent_id=agent.id,
        expires_at=now + CODE_VALIDITY,
        created_by=p.user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def create_agent(
    session: AsyncSession, p: Principal, data: AgentIn
) -> tuple[Agent, AgentEnrollmentCode]:
    p.require("agents.create")
    site = await site_in_scope(session, p, data.site_id)
    agent = Agent(
        reseller_id=site.reseller_id,
        site_id=site.id,
        name=data.name,
        kind=data.kind,
        update_channel=data.update_channel,
        priority=data.priority,
        state="offline",
        cluster_role="standby",
        # Agente recém-instalado informa versão aplicada 0: começar em 1 faz ele baixar a configuração.
        config_version=1,
    )
    session.add(agent)
    await session.flush()
    code = await _issue_code(session, p, agent)
    await audit.record(
        session,
        p,
        action="create",
        entity="agent",
        entity_id=agent.id,
        reseller_id=agent.reseller_id,
        after=audit.snapshot(agent),
    )
    return agent, code


async def regenerate_code(session: AsyncSession, p: Principal, agent_id: uuid.UUID) -> AgentEnrollmentCode:
    p.require("agents.update")
    agent = await get_agent(session, p, agent_id)
    code = await _issue_code(session, p, agent)
    await audit.record(
        session,
        p,
        action="enrollment_code",
        entity="agent",
        entity_id=agent.id,
        reseller_id=agent.reseller_id,
        after={"expires_at": code.expires_at},
    )
    return code


async def update_agent(session: AsyncSession, p: Principal, agent_id: uuid.UUID, data: AgentUpdate) -> Agent:
    p.require("agents.update")
    agent = await get_agent(session, p, agent_id)
    before = audit.snapshot(agent)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for k, v in changes.items():
        setattr(agent, k, v)
    if "monitor_local_networks" in changes and changes["monitor_local_networks"] != before.get(
        "monitor_local_networks"
    ):
        agent.config_version += 1  # o coletor busca a configuração nova no próximo heartbeat
    await session.flush()
    b, a = audit.diff(before, audit.snapshot(agent))
    await audit.record(
        session,
        p,
        action="update",
        entity="agent",
        entity_id=agent.id,
        reseller_id=agent.reseller_id,
        before=b,
        after=a,
    )
    return agent


async def revoke_agent(session: AsyncSession, p: Principal, agent_id: uuid.UUID) -> Agent:
    p.require("agents.update")
    agent = await get_agent(session, p, agent_id)
    if agent.revoked_at is not None:
        raise conflict("already_revoked", "Coletor já está revogado")
    agent.revoked_at = _now()
    agent.secret_hash = None
    agent.state = "offline"
    await _release_master(session, agent, reason="revoked", user_id=p.user_id)
    await audit.record(
        session, p, action="revoke", entity="agent", entity_id=agent.id, reseller_id=agent.reseller_id
    )
    # "A credencial para de funcionar imediatamente e o gateway derruba a conexão" (seção 4.2).
    await notify(session, CH_AGENT_REVOKED, str(agent.id))
    await emit_state(session, agent)
    return agent


async def delete_agent(session: AsyncSession, p: Principal, agent_id: uuid.UUID) -> None:
    p.require("agents.delete")
    agent = await get_agent(session, p, agent_id)
    agent.deleted_at = _now()
    agent.secret_hash = None
    agent.revoked_at = agent.revoked_at or _now()
    await _release_master(session, agent, reason="deleted", user_id=p.user_id)
    await audit.record(
        session, p, action="delete", entity="agent", entity_id=agent.id, reseller_id=agent.reseller_id
    )
    await notify(session, CH_AGENT_REVOKED, str(agent.id))
    await emit_state(session, agent)


async def emit_state(session: AsyncSession, agent: Agent) -> None:
    """Live event (portal SSE): the agent's state/role/presence changed."""
    site = await session.get(Site, agent.site_id)
    await notify_event(
        session,
        CH_AGENT_STATE,
        "agent",
        reseller_id=agent.reseller_id,
        customer_id=site.customer_id if site else None,
        id=agent.id,
        state=agent.state,
        cluster_role=agent.cluster_role,
    )


async def _release_master(
    session: AsyncSession, agent: Agent, *, reason: str, user_id: uuid.UUID | None
) -> None:
    site = await session.get(Site, agent.site_id)
    if site is not None and site.master_agent_id == agent.id:
        site.master_agent_id = None
        site.master_lease_expires_at = None
        agent.cluster_role = "standby"
        session.add(
            ClusterEvent(
                reseller_id=agent.reseller_id,
                site_id=site.id,
                from_agent_id=agent.id,
                to_agent_id=None,
                reason=reason,
                created_by=user_id,
            )
        )


async def bump_site_config(session: AsyncSession, site_id: uuid.UUID) -> None:
    """Configuration of the site changed: agents fetch it again on the next heartbeat."""
    await session.execute(
        update(Agent).where(Agent.site_id == site_id).values(config_version=Agent.config_version + 1)
    )


async def bump_all_configs(session: AsyncSession) -> None:
    await session.execute(update(Agent).values(config_version=Agent.config_version + 1))


# ----------------------------------------------------------------------------- enrollment / auth


async def check_enrollment_code(session: AsyncSession, code: str) -> proto.EnrollCheckResponse:
    """The installer validates the code (and shows where the collector will go) without using it."""
    now = _now()
    row = (
        await session.execute(
            select(AgentEnrollmentCode, Agent.name, Site.name, Customer.name)
            .join(Agent, Agent.id == AgentEnrollmentCode.agent_id)
            .join(Site, Site.id == AgentEnrollmentCode.site_id)
            .join(Customer, Customer.id == Site.customer_id)
            .where(AgentEnrollmentCode.code == code.upper(), Agent.deleted_at.is_(None))
        )
    ).tuples().one_or_none()  # fmt: skip
    if row is None or row[0].used_at is not None or row[0].expires_at <= now:
        raise unauthorized("enrollment_code_invalid", "Código de cadastro inválido, já usado ou expirado")
    code_row, agent_name, site_name, customer_name = row
    return proto.EnrollCheckResponse(
        agent_name=agent_name,
        customer_name=customer_name,
        site_name=site_name,
        expires_at=code_row.expires_at,
    )


async def enroll(
    session: AsyncSession, settings: Settings, req: proto.EnrollRequest, ip: str | None
) -> proto.EnrollResponse:
    now = _now()
    row = (
        await session.execute(
            select(AgentEnrollmentCode).where(AgentEnrollmentCode.code == req.code.upper()).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise unauthorized("enrollment_code_invalid", "Código de cadastro inválido, já usado ou expirado")
    agent = await session.get(Agent, row.agent_id, with_for_update=True)
    if agent is None or agent.deleted_at is not None:
        raise unauthorized("enrollment_code_invalid", "Código de cadastro inválido, já usado ou expirado")
    raw_secret = secrets.token_bytes(32)
    key = derive_agent_key(raw_secret)
    agent.secret_hash = base64.b64encode(
        crypto.encrypt(
            settings.master_key_bytes, base64.b64encode(key).decode(), associated_data=_aad(agent.id)
        )
    ).decode()
    agent.enrolled_at = now
    agent.revoked_at = None
    agent.hostname = req.hostname or None
    agent.os = req.os or None
    agent.arch = req.arch or None
    agent.kind = req.kind
    agent.version = req.version or None
    agent.local_ips = req.local_ips
    agent.host_mac = req.host_mac
    row.used_at = now
    await audit.record(
        session,
        None,
        action="agent.enroll",
        entity="agent",
        entity_id=agent.id,
        reseller_id=agent.reseller_id,
        after={"hostname": req.hostname, "os": req.os, "version": req.version, "ip": ip},
    )
    logger.info("coletor %s cadastrado (host %s)", agent.id, req.hostname)
    return proto.EnrollResponse(
        agent_id=str(agent.id),
        secret=base64.b64encode(raw_secret).decode(),
        server_time=now,
        ws_url=settings.agent_ws_url,
    )


class NonceCache:
    """In-memory replay protection for token requests (10-minute window; per process)."""

    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl = ttl_seconds
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_add(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._seen) > 100_000:  # noqa: PLR2004
                self._seen = {k: t for k, t in self._seen.items() if now - t < self.ttl}
            t = self._seen.get(key)
            if t is not None and now - t < self.ttl:
                return False
            self._seen[key] = now
            return True


def agent_key(settings: Settings, agent: Agent) -> bytes:
    if not agent.secret_hash:
        raise unauthorized("agent_revoked", "Coletor revogado ou não cadastrado")
    blob = base64.b64decode(agent.secret_hash)
    return base64.b64decode(crypto.decrypt(settings.master_key_bytes, blob, associated_data=_aad(agent.id)))


async def issue_token(
    session: AsyncSession, settings: Settings, req: proto.TokenRequest, nonces: NonceCache
) -> proto.TokenResponse:
    now = _now()
    try:
        agent_id = uuid.UUID(req.agent_id)
    except ValueError as exc:
        raise unauthorized("agent_invalid", "Coletor desconhecido") from exc
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise unauthorized("agent_invalid", "Coletor desconhecido")
    if agent.revoked_at is not None or not agent.secret_hash:
        raise unauthorized("agent_revoked", "Coletor revogado no portal; cadastre-o novamente")
    if abs(req.ts - int(now.timestamp())) > CLOCK_SKEW_LIMIT:
        raise AppError(
            401, "clock_skew", "Relógio do coletor muito diferente do servidor", server_time=now.isoformat()
        )
    expected = agent_signature(agent_key(settings, agent), req.agent_id, req.ts, req.nonce)
    if not hmac.compare_digest(expected, req.signature):
        logger.warning("assinatura inválida na autenticação do coletor %s", agent.id)
        raise unauthorized("signature_invalid", "Assinatura inválida")
    if not nonces.check_and_add(f"{agent.id}:{req.nonce}"):
        raise unauthorized("nonce_reused", "Requisição repetida")
    token, expires = create_agent_token(
        secret=settings.jwt_secret.get_secret_value(),
        agent_id=agent.id,
        reseller_id=agent.reseller_id,
        site_id=agent.site_id,
    )
    return proto.TokenResponse(access_token=token, expires_at=expires, server_time=now)


async def authenticate(session: AsyncSession, settings: Settings, authorization: str) -> Agent:
    """Validates "Bearer <agent JWT>" (HTTP channel and WebSocket handshake) and loads the agent."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized("token_missing", "Token do coletor ausente")
    try:
        claims = decode_agent_token(token, secret=settings.jwt_secret.get_secret_value())
    except InvalidTokenError as exc:
        raise unauthorized("token_invalid", f"Token do coletor inválido: {exc}") from exc
    agent = await session.get(Agent, claims.agent_id)
    if agent is None or agent.deleted_at is not None:
        raise unauthorized("agent_invalid", "Coletor desconhecido")
    if agent.revoked_at is not None:
        raise unauthorized("agent_revoked", "Coletor revogado no portal")
    return agent


# ----------------------------------------------------------------------------- heartbeat / config


async def _ensure_master(session: AsyncSession, agent: Agent, now: datetime) -> None:
    """Server-side lease (PROMPT 4.8): the MASTER renews it on every heartbeat. If the site has no master,
    the first agent that reports becomes master. Lease expiry/promotion is done by the worker."""
    site = await session.get(Site, agent.site_id, with_for_update=True)
    if site is None:
        return
    if site.master_agent_id is None:
        site.master_agent_id = agent.id
        site.master_lease_expires_at = now + LEASE_DURATION
        agent.cluster_role = "master"
        session.add(
            ClusterEvent(
                reseller_id=agent.reseller_id,
                site_id=site.id,
                from_agent_id=None,
                to_agent_id=agent.id,
                reason="first_agent",
            )
        )
        return
    if site.master_agent_id == agent.id:
        site.master_lease_expires_at = now + LEASE_DURATION
        agent.cluster_role = "master"
    else:
        agent.cluster_role = "standby"


async def heartbeat(
    session: AsyncSession, agent: Agent, req: proto.HeartbeatRequest, *, channel: str, ip: str | None = None
) -> proto.HeartbeatResponse:
    now = _now()
    before = (agent.state, agent.cluster_role)
    agent.last_seen_at = now
    if ip:
        agent.public_ip = ip  # IP de saída do cliente como o servidor o vê (seção 16.10)
    agent.install_path = req.install_path or agent.install_path
    agent.version = req.version or agent.version
    agent.hostname = req.hostname or agent.hostname
    agent.os = req.os or agent.os
    agent.arch = req.arch or agent.arch
    agent.local_ips = req.local_ips or agent.local_ips
    agent.host_mac = req.host_mac or agent.host_mac
    agent.queue_pending = req.queue_pending
    agent.uptime_seconds = req.uptime_seconds
    agent.cpu_percent = req.cpu_percent
    agent.memory_bytes = req.memory_bytes
    agent.applied_config_version = req.applied_config_version
    agent.last_error = "; ".join(req.errors)[:2000] or None
    if req.watchdog_state != "unknown":
        # Vigilância mútua (5.1): o que o coletor vê do serviço do watchdog, ao lado do que o watchdog relata.
        agent.watchdog_status = {**(agent.watchdog_status or {}), "service_state": req.watchdog_state}
    if req.latency_ms is not None:
        # Média móvel exponencial: desempate do failover prefere o coletor com menor latência (4.8).
        prev = agent.avg_latency_ms
        agent.avg_latency_ms = req.latency_ms if prev is None else round(prev * 0.8 + req.latency_ms * 0.2, 2)
    # A pausa é decidida no portal (agent.paused); o que o coletor informa é só o eco dela.
    if agent.paused:
        agent.state = "paused"
    elif req.queue_pending > DEGRADED_QUEUE or req.errors:
        agent.state = "degraded"
    else:
        agent.state = "online"
    await _ensure_master(session, agent, now)
    session.add(
        AgentHeartbeat(
            ts=now,
            agent_id=agent.id,
            reseller_id=agent.reseller_id,
            channel=channel,
            cpu_percent=req.cpu_percent,
            memory_bytes=req.memory_bytes,
            queue_pending=req.queue_pending,
            uptime_seconds=req.uptime_seconds,
            version=req.version,
            cluster_role=agent.cluster_role,
            latency_ms=req.latency_ms,
        )
    )
    if (agent.state, agent.cluster_role) != before:
        await emit_state(session, agent)
    return proto.HeartbeatResponse(
        server_time=now,
        config_version=agent.config_version,
        cluster_role="master" if agent.cluster_role == "master" else "standby",
        paused=agent.paused,
    )


async def agent_stats(session: AsyncSession, p: Principal, agent_id: uuid.UUID) -> AgentStats:
    agent = await get_agent(session, p, agent_id)
    since = _now() - timedelta(hours=24)
    items = (
        await session.execute(
            select(
                func.count().filter(ReadingIdempotency.kind == "reading"),
                func.count(),
                func.max(ReadingIdempotency.created_at),
            ).where(
                ReadingIdempotency.agent_id == agent.id,
                ReadingIdempotency.result == "accepted",
                ReadingIdempotency.created_at >= since,
            )
        )
    ).one()
    failures = (
        await session.execute(
            select(func.count())
            .select_from(DeviceEvent)
            .where(
                DeviceEvent.reseller_id == agent.reseller_id,
                DeviceEvent.type == "read_failed",
                DeviceEvent.created_at >= since,
                DeviceEvent.data["agent_id"].astext == str(agent.id),
            )
        )
    ).scalar_one()
    devices = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(or_(Device.last_status == "offline", Device.disconnected.is_(True))),
            ).where(
                Device.last_agent_id == agent.id,
                Device.deleted_at.is_(None),
                Device.active.is_(True),
                Device.discovery_state == "approved",
            )
        )
    ).one()
    return AgentStats(
        readings_24h=items[0],
        items_24h=items[1],
        failures_24h=failures,
        devices_total=devices[0],
        devices_offline=devices[1],
        last_item_at=items[2],
    )


async def ignored_serials(session: AsyncSession, reseller_id: uuid.UUID) -> list[str]:
    """Serials discarded in Descobertas (16.1): the agents stop reading them."""
    rows = await session.execute(
        select(Device.serial)
        .where(
            Device.reseller_id == reseller_id,
            Device.discovery_state == "discarded",
            Device.deleted_at.is_(None),
        )
        .order_by(Device.serial)
    )
    return list(rows.scalars())


def collection_settings(site: Site) -> dict[str, Any]:
    merged = dict(DEFAULT_COLLECTION)
    merged.update({k: v for k, v in (site.collection_config or {}).items() if v is not None})
    return merged


async def agent_config(session: AsyncSession, settings: Settings, agent: Agent) -> proto.AgentConfig:
    site = await session.get(Site, agent.site_id)
    if site is None:
        raise not_found("Local")
    cfg = collection_settings(site)
    ranges = (
        await session.execute(
            select(IpRange)
            .where(IpRange.site_id == site.id, IpRange.status == "approved", IpRange.active.is_(True))
            .order_by(IpRange.created_at)
        )
    ).scalars()
    creds = (
        await session.execute(
            select(SnmpCredential).where(SnmpCredential.site_id == site.id).order_by(SnmpCredential.position)
        )
    ).scalars()
    key = settings.master_key_bytes

    def dec(blob: bytes | None, cred: SnmpCredential) -> str | None:
        return crypto.decrypt(key, blob, associated_data=snmp_aad(cred.site_id)) if blob else None

    profiles = await active_profiles(session)
    return proto.AgentConfig(
        config_version=agent.config_version,
        site_id=str(site.id),
        cluster_role="master" if agent.cluster_role == "master" else "standby",
        paused=agent.paused,
        intervals=proto.Intervals(
            discovery_minutes=cfg["discovery_minutes"],
            counters_minutes=cfg["counters_minutes"],
            supplies_minutes=cfg["supplies_minutes"],
            status_minutes=cfg["status_minutes"],
            attributes_minutes=cfg["attributes_minutes"],
        ),
        discovery=proto.DiscoveryConfig(
            concurrency=cfg["discovery_concurrency"],
            rate_pps=cfg["discovery_rate_pps"],
            timeout_ms=cfg["snmp_timeout_ms"],
            retries=min(int(cfg["snmp_retries"]), MAX_SNMP_RETRIES),
            read_timeout_ms=cfg["snmp_read_timeout_ms"],
        ),
        ranges=[
            proto.IpRangeConfig(
                id=str(r.id),
                cidr=r.cidr,
                start_ip=r.start_ip,
                end_ip=r.end_ip,
                host=r.host,
                exclusions=list(r.exclusions),
                ports=list(r.ports),
            )
            for r in ranges
        ],
        credentials=[
            proto.CredentialConfig(
                id=str(c.id),
                version=c.version,
                community=dec(c.community_enc, c),
                v3_username=c.v3_username,
                v3_auth_protocol=c.v3_auth_protocol,
                v3_auth_password=dec(c.v3_auth_password_enc, c),
                v3_priv_protocol=c.v3_priv_protocol,
                v3_priv_password=dec(c.v3_priv_password_enc, c),
            )
            for c in creds
        ],
        profiles=profiles,
        proxy_url=cfg.get("proxy_url"),
        keep_awake=bool(cfg.get("keep_awake")),
        ws_url=settings.agent_ws_url,
        monitor_local_networks=agent.monitor_local_networks,
        ignored_serials=await ignored_serials(session, agent.reseller_id),
    )


def snmp_aad(site_id: uuid.UUID) -> bytes:
    return b"snmp:" + site_id.bytes


async def active_profiles(session: AsyncSession) -> list[dict[str, Any]]:
    """Latest active version of every profile, as JSON (the version inside is the DB version)."""
    latest = (
        select(ReadProfile.profile_key, func.max(ReadProfile.version).label("v"))
        .where(ReadProfile.active.is_(True))
        .group_by(ReadProfile.profile_key)
        .subquery()
    )
    rows = (
        await session.execute(
            select(ReadProfile).join(
                latest,
                (ReadProfile.profile_key == latest.c.profile_key) & (ReadProfile.version == latest.c.v),
            )
        )
    ).scalars()
    return [{**r.content, "version": r.version} for r in sorted(rows, key=lambda r: r.profile_key)]


async def suggest_ranges(session: AsyncSession, agent: Agent, req: proto.SuggestRangesRequest) -> int:
    import ipaddress  # noqa: PLC0415

    created = 0
    valid: list[str] = []
    for cidr in req.ranges:
        try:
            net = ipaddress.IPv4Network(cidr, strict=False)
        except ValueError:
            continue
        if not net.is_private or net.prefixlen < 16:  # noqa: PLR2004
            continue
        valid.append(str(net))
        exists = (
            await session.execute(
                select(func.count())
                .select_from(IpRange)
                .where(IpRange.site_id == agent.site_id, IpRange.cidr == str(net))
            )
        ).scalar_one()
        if not exists:
            session.add(
                IpRange(
                    reseller_id=agent.reseller_id,
                    site_id=agent.site_id,
                    cidr=str(net),
                    status="suggested",
                    active=False,
                    suggested_by_agent_id=agent.id,
                )
            )
            created += 1
    if not valid:
        raise bad_request("no_valid_ranges", "Nenhuma sub-rede privada válida foi sugerida")
    agent.suggested_ranges = valid
    return created
