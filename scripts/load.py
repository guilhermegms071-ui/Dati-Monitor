"""Teste de carga (PROMPT 13) contra um ambiente isolado.

Recria o banco `dati_load` (nunca o de desenvolvimento), sobe API (8200) e gateway (8201) com ele e então:
  1. cadastra 500 coletores (um por local, 50 clientes) pelo mesmo caminho do dm-agent (enroll + token);
  2. abre os 500 WebSockets ao mesmo tempo e os mantém com heartbeat durante todo o teste;
  3. envia N "horas" de leituras de 20.000 equipamentos (40 por coletor, lotes assinados como os do
     agente) e mede quanto o backend leva para receber cada hora;
  4. mede a tela de parque com as 20.000 linhas (lista com ordenações, filtros, 2ª página e contagens).
Falha se algum WebSocket cair ou não ficar registrado, se alguma leitura for recusada, se uma hora de
leituras levar mais que --max-hour-minutes ou se alguma consulta do parque passar de 1 s.

Só desenvolvimento/CI (recusa APP_ENV=production). O banco `dati_load` é apagado no fim (--keep-db mantém).
Uso: .venv\\Scripts\\python scripts\\load.py [--agents 500 --devices 20000 --hours 2]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import gzip
import json
import os
import secrets
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from sqlalchemy.engine import make_url
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402
from app.core.permissions import RESELLER_ADMIN  # noqa: E402
from app.core.security import agent_signature, derive_agent_key, hash_password  # noqa: E402
from app.models import Reseller, User  # noqa: E402
from app.services.bootstrap import ensure_bootstrap  # noqa: E402
from app.services.catalog import sync_brands, sync_profiles  # noqa: E402

DB_NAME = "dati_load"
API_PORT, GW_PORT = 8200, 8201
API = f"http://127.0.0.1:{API_PORT}"
WS_URL = f"ws://127.0.0.1:{GW_PORT}/ws/agent"
EMAIL = "carga@dati.local"
CUSTOMERS = 50
PARK_LIMIT_SECONDS = 1.0
PARK_RUNS = 5
HTTP_UNAUTHORIZED, HTTP_TOO_MANY = 401, 429
OUT = REPO / "var" / "load"


def log(msg: str) -> None:
    sys.stdout.write(f"{datetime.now():%H:%M:%S} {msg}\n")
    sys.stdout.flush()


def fail(msg: str) -> None:
    sys.stderr.write(f"ERRO: {msg}\n")
    raise SystemExit(1)


def env_file() -> dict[str, str]:
    path = REPO / ".env"
    values: dict[str, str] = {}
    if not path.exists():  # CI: tudo vem das variáveis de ambiente
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.split(" #", 1)[0].strip()
    return values


def superuser() -> dict[str, Any]:
    """Superusuário do PostgreSQL (recria o banco dati_load): variável de ambiente ou .env."""
    file = env_file()

    def get(name: str, default: str | None = None) -> str:
        value = os.environ.get(name) or file.get(name) or default
        if value is None:
            fail(f"{name} não definido (.env ou variável de ambiente)")
            raise AssertionError
        return value

    return {
        "host": get("POSTGRES_HOST", "127.0.0.1"),
        "port": int(get("POSTGRES_PORT", "5432")),
        "user": get("POSTGRES_SUPERUSER"),
        "password": get("POSTGRES_SUPERUSER_PASSWORD"),
        "database": "postgres",
    }


def load_db_url() -> str:
    """Mesmo servidor/usuário do .env, banco dati_load (asyncpg/SQLAlchemy)."""
    return make_url(get_settings().database_url).set(database=DB_NAME).render_as_string(hide_password=False)


def plain_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ----------------------------------------------------------------------------- banco


async def recreate_db() -> None:
    app_user = make_url(get_settings().database_url).username
    conn = await asyncpg.connect(**superuser())
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
        await conn.execute(
            f"CREATE DATABASE {DB_NAME} OWNER \"{app_user}\" ENCODING 'UTF8' TEMPLATE template0"
        )
    finally:
        await conn.close()


async def drop_db() -> None:
    conn = await asyncpg.connect(**superuser())
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
    finally:
        await conn.close()


def run_cli(command: str, env: dict[str, str]) -> None:
    r = subprocess.run(  # noqa: S603 - comando do próprio repositório
        [sys.executable, "-m", "app.cli", command],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if r.returncode != 0:
        fail(f"app.cli {command} falhou: {r.stderr[-2000:]}")


async def seed_user(db_url: str, password: str) -> None:
    engine = make_engine(db_url)
    try:
        async with make_sessionmaker(engine)() as session:
            await ensure_bootstrap(session, get_settings())
            await sync_brands(session)
            await sync_profiles(session)
            reseller = (await session.execute(Reseller.__table__.select().limit(1))).first()
            if reseller is None:
                fail("o bootstrap não criou a revenda")
                return
            session.add(
                User(
                    reseller_id=reseller.id,
                    name="Usuário do teste de carga",
                    email=EMAIL,
                    password_hash=hash_password(password),
                    role_code=RESELLER_ADMIN,
                    active=True,
                    must_change_password=False,
                    password_changed_at=datetime.now(UTC),
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def scalar(db_url: str, sql: str) -> int:
    conn = await asyncpg.connect(plain_url(db_url))
    try:
        return int(await conn.fetchval(sql))
    finally:
        await conn.close()


# ----------------------------------------------------------------------------- servidores


class Servers:
    def __init__(self, env: dict[str, str], logs: Path) -> None:
        self.env = env
        self.logs = logs
        self.procs: list[subprocess.Popen[bytes]] = []
        self.files: list[Any] = []

    def start(self) -> None:
        for name, module, port, extra in (
            ("api", "app.api.main:create_app", API_PORT, []),
            ("gateway", "app.gateway.main:create_app", GW_PORT, ["--ws", "websockets-sansio"]),
        ):
            f = (self.logs / f"{name}.log").open("ab")
            self.files.append(f)
            args = [sys.executable, "-m", "uvicorn", module, "--factory", "--host", "127.0.0.1"]
            args += ["--port", str(port), "--log-level", "warning", *extra]
            self.procs.append(
                subprocess.Popen(args, cwd=BACKEND, env=self.env, stdout=f, stderr=subprocess.STDOUT)  # noqa: S603
            )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if any(p.poll() is not None for p in self.procs):
                fail(f"API/gateway do teste de carga caíram ao subir; veja {self.logs}")
            with contextlib.suppress(httpx.HTTPError):
                if (
                    httpx.get(f"{API}/api/health", timeout=3).json().get("status") == "ok"
                    and httpx.get(f"http://127.0.0.1:{GW_PORT}/health", timeout=3).status_code == 200  # noqa: PLR2004
                ):
                    return
            time.sleep(1)
        fail(f"API/gateway do teste de carga não subiram em 90 s; veja {self.logs}")

    def alive(self) -> bool:
        return all(p.poll() is None for p in self.procs)

    def stop(self) -> None:
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in self.files:
            f.close()


# ----------------------------------------------------------------------------- coletores simulados


@dataclass
class SimAgent:
    index: int
    agent_id: str = ""
    secret: bytes = b""
    token: str = ""
    seq: int = 0
    welcomed: bool = False
    closed_reason: str = ""
    errors: list[str] = field(default_factory=list)
    acks: list[float] = field(default_factory=list)

    def token_body(self) -> dict[str, Any]:
        ts, nonce = int(time.time()), secrets.token_hex(16)
        sig = agent_signature(derive_agent_key(self.secret), self.agent_id, ts, nonce)
        return {"v": 1, "agent_id": self.agent_id, "ts": ts, "nonce": nonce, "signature": sig}


@dataclass
class Stats:
    retries_429: int = 0
    retries_network: int = 0
    rejected: list[str] = field(default_factory=list)


NETWORK_ATTEMPTS = 5


async def post_net(client: httpx.AsyncClient, url: str, stats: Stats, **kw: Any) -> httpx.Response:
    """POST que repete em falha de rede, como o dm-agent (backoff): com centenas de conexões, o servidor
    pode fechar uma conexão keep-alive no instante em que o cliente a reutiliza. Cada repetição é contada
    no relatório; esgotadas as tentativas, o erro sobe."""
    for attempt in range(NETWORK_ATTEMPTS):
        try:
            return await client.post(url, **kw)
        except httpx.TransportError:
            if attempt == NETWORK_ATTEMPTS - 1:
                raise
            stats.retries_network += 1
            await asyncio.sleep(2**attempt)
    raise AssertionError  # inalcançável


async def post_retry(client: httpx.AsyncClient, url: str, stats: Stats, **kw: Any) -> httpx.Response:
    """Cadastro e token têm limite por IP (todos os 500 coletores aqui vêm do mesmo IP): espera e repete."""
    for _ in range(30):
        r = await post_net(client, url, stats, **kw)
        if r.status_code != HTTP_TOO_MANY:
            return r
        stats.retries_429 += 1
        await asyncio.sleep(5)
    return r


async def refresh_token(client: httpx.AsyncClient, a: SimAgent, stats: Stats) -> None:
    r = await post_retry(client, "/api/agent/token", stats, json=a.token_body())
    if r.status_code != 200:  # noqa: PLR2004
        fail(f"token do coletor {a.index} recusado: {r.status_code} {r.text[:300]}")
    a.token = r.json()["access_token"]


async def setup_agents(
    portal: httpx.AsyncClient, client: httpx.AsyncClient, n: int, stats: Stats
) -> list[SimAgent]:
    company = (
        await portal.post("/api/v1/companies", json={"legal_name": "Empresa carga"})
    ).raise_for_status()
    company_id = company.json()["id"]
    customers = []
    for i in range(CUSTOMERS):
        r = await portal.post(
            "/api/v1/customers", json={"name": f"Cliente carga {i:02d}", "company_id": company_id}
        )
        customers.append(r.raise_for_status().json()["id"])
    agents = [SimAgent(i) for i in range(n)]
    sem = asyncio.Semaphore(20)

    async def one(a: SimAgent) -> None:
        async with sem:
            site = await portal.post(
                "/api/v1/sites",
                json={
                    "customer_id": customers[a.index % CUSTOMERS],
                    "name": f"Local carga {a.index:03d}",
                    "auto_activate_devices": True,
                },
            )
            site_id = site.raise_for_status().json()["id"]
            created = await portal.post(
                "/api/v1/agents", json={"site_id": site_id, "name": f"Coletor {a.index:03d}"}
            )
            code = created.raise_for_status().json()["enrollment"]["code"]
            body = {
                "v": 1,
                "code": code,
                "hostname": f"PC-CARGA-{a.index:03d}",
                "os": "Windows 11",
                "arch": "amd64",
                "kind": "windows",
                "version": "1.0.0-carga",
                "local_ips": [f"10.{a.index // 250}.{a.index % 250}.10"],
            }
            r = await post_retry(client, "/api/agent/enroll", stats, json=body)
            if r.status_code != 200:  # noqa: PLR2004
                fail(f"enroll do coletor {a.index} falhou: {r.status_code} {r.text[:300]}")
            a.agent_id, a.secret = r.json()["agent_id"], base64.b64decode(r.json()["secret"])
            await refresh_token(client, a, stats)

    await asyncio.gather(*(one(a) for a in agents))
    return agents


async def ws_session(a: SimAgent, stop: asyncio.Event, on_welcome: Callable[[], None]) -> None:
    """Um coletor no WebSocket: hello e heartbeat a cada 30 s, como o dm-agent."""
    try:
        async with connect(
            WS_URL, additional_headers={"Authorization": f"Bearer {a.token}"}, open_timeout=60, max_size=None
        ) as ws:
            welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            if welcome.get("type") != "welcome":
                a.errors.append(f"primeira mensagem não foi welcome: {welcome}")
                return
            a.welcomed = True
            on_welcome()
            await ws.send(json.dumps({"v": 1, "type": "hello", "data": {"v": 1, "version": "1.0.0-carga"}}))
            await asyncio.gather(_ws_reader(a, ws), _ws_heartbeats(a, ws, stop))
    except ConnectionClosed as exc:
        a.closed_reason = f"fechado pelo servidor ({exc.rcvd.code if exc.rcvd else '?'})"
    except (OSError, TimeoutError) as exc:
        a.closed_reason = f"{type(exc).__name__}: {exc}"


_sent_at: dict[str, float] = {}


async def _ws_heartbeats(a: SimAgent, ws: ClientConnection, stop: asyncio.Event) -> None:
    while not stop.is_set():
        _sent_at[a.agent_id] = time.monotonic()
        data = {
            "v": 1,
            "ts": datetime.now(UTC).isoformat(),
            "version": "1.0.0-carga",
            "cluster_role": "master",
        }
        await ws.send(json.dumps({"v": 1, "type": "heartbeat", "data": data}))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=30)
    await ws.close()


async def _ws_reader(a: SimAgent, ws: ClientConnection) -> None:
    with contextlib.suppress(ConnectionClosed):
        async for raw in ws:
            msg = json.loads(raw)
            match msg.get("type"):
                case "heartbeat_ack":
                    a.acks.append(time.monotonic() - _sent_at.get(a.agent_id, time.monotonic()))
                case "error":
                    a.errors.append(str(msg.get("data")))
                case _:
                    pass


def reading_items(a: SimAgent, per_agent: int, hour: int, read_at: datetime) -> list[dict[str, Any]]:
    items = []
    for d in range(per_agent):
        a.seq += 1
        base = 10_000 + (a.index * per_agent + d) * 7
        mono, color = base + hour * 120, base // 3 + hour * 40
        items.append(
            {
                "key": f"{a.agent_id}:{a.seq}",
                "kind": "reading",
                "read_at": read_at.isoformat(),
                "device": {
                    "ip": f"10.{a.index // 250}.{a.index % 250}.{d + 20}",
                    "port": 161,
                    "serial": f"CARGA{a.index:03d}{d:03d}",
                    "mac": f"00:CA:{a.index // 256:02X}:{a.index % 256:02X}:00:{d:02X}",
                    "hostname": f"IMP{d:03d}",
                    "sys_object_id": "1.3.6.1.4.1.18334.1.2",
                    "model": "bizhub C287",
                    "profile_key": "konica-minolta",
                },
                "reading": {
                    "counters": {"total": mono + color, "mono": mono, "color": color},
                    "counter_source": "konica_counters",
                    "profile_key": "konica-minolta",
                    "profile_version": 1,
                    "mono_only": False,
                    "sum_tolerance_percent": 2,
                    "status": "ready",
                    "error_bits": 0,
                    "source": "snmp",
                },
            }
        )
    return items


@dataclass
class Hour:
    """Uma hora de leituras: horário das leituras e índice (para os contadores crescerem)."""

    index: int
    read_at: datetime


async def send_hour(
    client: httpx.AsyncClient, agents: list[SimAgent], per_agent: int, hour: Hour, stats: Stats
) -> None:
    sem = asyncio.Semaphore(25)

    async def one(a: SimAgent) -> None:
        items = reading_items(a, per_agent, hour.index, hour.read_at)
        raw = gzip.compress(json.dumps({"v": 1, "items": items}).encode())
        async with sem:
            for _ in range(2):
                headers = {
                    "Authorization": f"Bearer {a.token}",
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                }
                r = await post_net(client, "/api/agent/readings", stats, content=raw, headers=headers)
                if r.status_code == HTTP_UNAUTHORIZED:  # token de 15 min venceu: renova como o agente
                    await refresh_token(client, a, stats)
                    continue
                break
            if r.status_code != 200:  # noqa: PLR2004
                stats.rejected.append(f"coletor {a.index}: HTTP {r.status_code} {r.text[:200]}")
                return
            bad = [x for x in r.json()["results"] if x["status"] not in ("accepted", "duplicate")]
            stats.rejected += [f"coletor {a.index}: {x}" for x in bad[:3]]

    await asyncio.gather(*(one(a) for a in agents))


# ----------------------------------------------------------------------------- parque


PARK_QUERIES: list[tuple[str, str, dict[str, Any]]] = [
    ("lista (serial)", "/api/v1/park", {}),
    ("ordenado por total desc", "/api/v1/park", {"sort": "total", "direction": "desc"}),
    ("ordenado por cliente", "/api/v1/park", {"sort": "customer"}),
    ("ordenado por última leitura", "/api/v1/park", {"sort": "last_read_at", "direction": "desc"}),
    ("busca livre", "/api/v1/park", {"q": "CARGA123"}),
    ("filtro de modelo", "/api/v1/park", {"model": "bizhub", "sort": "ip"}),
    ("contagens", "/api/v1/park/counts", {}),
]


async def measure_park(portal: httpx.AsyncClient, devices: int) -> tuple[dict[str, Any], list[str]]:
    results: dict[str, Any] = {}
    problems: list[str] = []
    first = (await portal.get("/api/v1/park", params={"limit": 100})).raise_for_status().json()
    if first["total"] != devices:
        problems.append(f"parque mostra {first['total']} equipamentos (esperado {devices})")
    queries = [*PARK_QUERIES, ("2ª página", "/api/v1/park", {"cursor": first["next_cursor"]})]
    for label, path, params in queries:
        times = []
        for _ in range(PARK_RUNS + 1):  # a 1ª execução aquece o cache do Postgres e é descartada
            t0 = time.perf_counter()
            r = await portal.get(path, params={"limit": 100, **params} if path.endswith("park") else params)
            elapsed = time.perf_counter() - t0
            r.raise_for_status()
            times.append(elapsed)
        times = times[1:]
        worst, median = max(times), statistics.median(times)
        results[label] = {"max_s": round(worst, 3), "median_s": round(median, 3)}
        log(f"parque {label}: mediana {median * 1000:.0f} ms, pior {worst * 1000:.0f} ms")
        if worst >= PARK_LIMIT_SECONDS:
            problems.append(f"parque '{label}' levou {worst:.2f} s (limite 1 s)")
    return results, problems


# ----------------------------------------------------------------------------- principal


class Fleet:
    """Os coletores simulados com o WebSocket aberto durante todo o teste."""

    def __init__(self, agents: list[SimAgent]) -> None:
        self.agents = agents
        self.stop = asyncio.Event()
        self.all_welcomed = asyncio.Event()
        self.tasks: list[asyncio.Task[None]] = []

    async def open(self, db_url: str, problems: list[str]) -> None:
        t0 = time.monotonic()
        for a in self.agents:  # rampa: 50 conexões por segundo
            self.tasks.append(asyncio.create_task(self._session(a)))
            if a.index % 50 == 49:  # noqa: PLR2004
                await asyncio.sleep(1)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.all_welcomed.wait(), timeout=120)
        n = len(self.agents)
        welcomed = sum(a.welcomed for a in self.agents)
        presence = await scalar(db_url, "SELECT count(*) FROM agent_presence")
        log(f"WebSockets: {welcomed}/{n} conectados em {time.monotonic() - t0:.0f} s; presença {presence}")
        if welcomed < n or presence < n:
            problems.append(f"só {welcomed} WebSockets abertos / {presence} presenças (esperado {n})")

    async def _session(self, a: SimAgent) -> None:
        await ws_session(a, self.stop, self._welcomed)

    def _welcomed(self) -> None:
        if all(a.welcomed for a in self.agents):
            self.all_welcomed.set()

    async def close(self, db_url: str, problems: list[str]) -> dict[str, Any]:
        n = len(self.agents)
        still = sum(1 for t in self.tasks if not t.done())
        presence_end = await scalar(db_url, "SELECT count(*) FROM agent_presence")
        self.stop.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        dropped = [f"{a.index}: {a.closed_reason}" for a in self.agents if a.closed_reason]
        ws_errors = [f"{a.index}: {e}" for a in self.agents for e in a.errors]
        acks = sorted(x for a in self.agents for x in a.acks)
        if still < n or dropped:
            problems.append(f"{n - still} WebSocket(s) caíram; ex.: {dropped[:3]}")
        if presence_end < n:
            problems.append(f"presença no fim: {presence_end} (esperado {n})")
        if ws_errors:
            problems.append(f"{len(ws_errors)} erro(s) do gateway; ex.: {ws_errors[:3]}")
        if not acks:
            problems.append("nenhum heartbeat_ack recebido")
        p95 = acks[int(len(acks) * 0.95)] if acks else 0.0
        log(f"heartbeats: {len(acks)} respostas, p95 {p95 * 1000:.0f} ms")
        return {"heartbeat_acks": len(acks), "heartbeat_p95_ms": round(p95 * 1000)}


async def ingest(
    client: httpx.AsyncClient, agents: list[SimAgent], args: argparse.Namespace, problems: list[str]
) -> tuple[list[dict[str, Any]], Stats]:
    stats = Stats()
    per_agent = args.devices // args.agents
    total = per_agent * len(agents)
    hours: list[dict[str, Any]] = []
    start_at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=args.hours)
    for h in range(args.hours):
        t0 = time.monotonic()
        await send_hour(client, agents, per_agent, Hour(h, start_at + timedelta(hours=h, minutes=5)), stats)
        secs = time.monotonic() - t0
        hours.append(
            {"hour": h + 1, "seconds": round(secs, 1), "readings_per_second": round(total / secs, 1)}
        )
        log(f"hora {h + 1}: {total} leituras em {secs:.0f} s ({total / secs:.0f}/s)")
        if secs > args.max_hour_minutes * 60:
            problems.append(f"hora {h + 1} levou {secs / 60:.1f} min (limite {args.max_hour_minutes} min)")
    if stats.rejected:
        problems.append(f"{len(stats.rejected)} lote(s)/leitura(s) recusados; ex.: {stats.rejected[:3]}")
    return hours, stats


async def check_db(db_url: str, expected: int, hours: int, problems: list[str]) -> None:
    devices = await scalar(db_url, "SELECT count(*) FROM devices WHERE deleted_at IS NULL AND active")
    readings = await scalar(db_url, "SELECT count(*) FROM readings")
    log(f"banco: {devices} equipamentos ativos, {readings} leituras")
    if devices != expected or readings != expected * hours:
        problems.append(
            f"banco com {devices} equipamentos/{readings} leituras (esperado {expected}/{expected * hours})"
        )


async def scenario(args: argparse.Namespace, db_url: str, password: str, servers: Servers) -> dict[str, Any]:
    problems: list[str] = []
    setup_stats = Stats()
    expected = args.devices // args.agents * args.agents
    async with (
        httpx.AsyncClient(base_url=API, timeout=120, limits=httpx.Limits(max_connections=60)) as client,
        httpx.AsyncClient(base_url=API, timeout=120) as portal,
    ):
        login = await portal.post("/api/v1/auth/login", json={"email": EMAIL, "password": password})
        portal.headers["Authorization"] = f"Bearer {login.raise_for_status().json()['access_token']}"
        t0 = time.monotonic()
        agents = await setup_agents(portal, client, args.agents, setup_stats)
        waits = setup_stats.retries_429
        log(f"{len(agents)} coletores cadastrados em {time.monotonic() - t0:.0f} s ({waits} esperas por 429)")
        fleet = Fleet(agents)
        await fleet.open(db_url, problems)
        hours, stats = await ingest(client, agents, args, problems)
        await check_db(db_url, expected, args.hours, problems)
        park, park_problems = await measure_park(portal, expected)
        problems += park_problems
        await asyncio.sleep(args.hold)  # mais heartbeats com tudo carregado
        ws = await fleet.close(db_url, problems)
        if not servers.alive():
            problems.append("API ou gateway caíram durante o teste")
        return {
            "agents": len(agents),
            "devices": expected,
            "hours": hours,
            "park": park,
            **ws,
            "retries_429": waits + stats.retries_429,
            "retries_network": setup_stats.retries_network + stats.retries_network,
            "problems": problems,
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agents", type=int, default=500)
    ap.add_argument("--devices", type=int, default=20_000)
    ap.add_argument(
        "--hours", type=int, default=2, help="horas de leituras enviadas (a 1ª cria os equipamentos)"
    )
    ap.add_argument(
        "--max-hour-minutes", type=float, default=15, help="limite para receber 1 hora de leituras"
    )
    ap.add_argument("--hold", type=float, default=60, help="segundos extras com os WebSockets abertos no fim")
    ap.add_argument("--keep-db", action="store_true", help="não apaga o banco dati_load no fim")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if get_settings().app_env == "production":
        fail("teste de carga só para desenvolvimento/CI")
    if args.devices % args.agents:
        fail("--devices precisa ser múltiplo de --agents")
    work = OUT / datetime.now().strftime("%Y%m%d-%H%M%S")
    work.mkdir(parents=True)
    db_url = load_db_url()
    password = "Carga-" + secrets.token_urlsafe(16)  # só em memória
    env = {k: v for k, v in os.environ.items() if k != "__COMPAT_LAYER"}
    env.update(
        DATABASE_URL=db_url,
        PUBLIC_SERVER_URL=API,
        PUBLIC_WS_URL=WS_URL,
        PYTHONIOENCODING="utf-8",
        LOG_LEVEL="WARNING",
    )
    log(f"recriando o banco {DB_NAME}")
    asyncio.run(recreate_db())
    run_cli("migrate", env)
    run_cli("ensure-partitions", env)
    asyncio.run(seed_user(db_url, password))
    servers = Servers(env, work)
    servers.start()
    log(f"API {API} e gateway {WS_URL} no ar (logs em {work})")
    try:
        result = asyncio.run(scenario(args, db_url, password, servers))
    finally:
        servers.stop()
        if not args.keep_db:
            asyncio.run(drop_db())
    result.update(finished_at=datetime.now(UTC).isoformat(), ok=not result["problems"])
    (work / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    for p in result["problems"]:
        sys.stderr.write(f"FALHOU: {p}\n")
    if result["problems"]:
        return 1
    log(f"OK: carga de {args.devices} equipamentos e {args.agents} WebSockets")
    log(f"relatório: {work / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
