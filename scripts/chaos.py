"""Teste de caos (PROMPT seção 13) contra um ambiente real e isolado.

Sobe API (8100), gateway (8101), worker e as 8 impressoras simuladas (12161-12168); cadastra dois
coletores REAIS (dm-agent compilado do código) no mesmo local, cada um vigiado por um dm-watchdog em
modo processo, e então provoca, nesta ordem:
  1. morte do processo do coletor MASTER  → o watchdog o reinicia e informa o motivo;
  2. queda de "internet" (API e gateway fora do ar por N min) → as leituras ficam na fila local e
     chegam todas quando o servidor volta;
  3. reinício do PostgreSQL (terminal de administrador) ou, sem administrador, todas as conexões do
     banco derrubadas → API, gateway (LISTEN) e worker se recuperam sozinhos;
  4. queda do PC do MASTER (coletor e watchdog mortos) → o lease expira, o STANDBY assume e passa a ler;
     o antigo MASTER volta como STANDBY.
Verifica: nenhuma leitura perdida (fila local zerada, nada em dead-letter, leituras do período da queda
no banco), nenhuma duplicidade entre coletores e recuperação sem intervenção. Relatório OK/FALHOU por
item; código de saída 1 se algo falhar.

Uso: .venv\\Scripts\\python scripts\\chaos.py [--outage-minutes 10]   (ou scripts\\chaos.ps1)
Só para desenvolvimento: usa o banco de desenvolvimento e o usuário de teste do E2E. Rode com o dev.ps1
parado: o worker dele poderia atualizar automaticamente os coletores do teste no meio da medição.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from e2e_seed import E2E_EMAIL, ensure_user  # noqa: E402

from app.core.config import get_settings  # noqa: E402

WORK = REPO / "var" / "chaos"
VENV_PY = REPO / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
API = "http://127.0.0.1:8100"
GW_PORT = 8101
SIM_READY = "http://127.0.0.1:12160/"
SIM_PORTS = list(range(12161, 12169))
CUSTOMER, SITE, COMPANY = "Cliente Caos", "Local Caos", "Empresa Caos"
COUNTERS_MINUTES = 5
EXE = ".exe" if sys.platform == "win32" else ""
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
PRINTERS = 8


# ----------------------------------------------------------------------------- relatório


@dataclass
class Report:
    items: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append((name, ok, detail))
        log(f"{'OK     ' if ok else 'FALHOU '} {name}" + (f" — {detail}" if detail else ""))
        return ok

    def failed(self) -> list[str]:
        return [n for n, ok, _ in self.items if not ok]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)  # noqa: T201


def wait_until(what: str, cond: Callable[[], bool], timeout: float, every: float = 2.0) -> float | None:
    """Seconds until cond() is true, or None on timeout (the caller reports it)."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        # Falhas passageiras (servidor subindo, banco reconectando) não encerram a espera.
        with contextlib.suppress(httpx.HTTPError, OSError, ValueError, KeyError, RuntimeError):
            if cond():
                return time.monotonic() - start
        time.sleep(every)
    log(f"tempo esgotado ({timeout:.0f} s) esperando: {what}")
    return None


# ----------------------------------------------------------------------------- processos


def child_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "__COMPAT_LAYER"}  # como num PC de cliente
    env.update(extra)
    return env


class Stack:
    def __init__(self) -> None:
        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self.logs: dict[str, Any] = {}

    def start(self, name: str, args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
        log_path = WORK / "logs" / f"{name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        f = log_path.open("ab")
        self.logs[name] = f
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.procs[name] = subprocess.Popen(  # noqa: S603 - comandos montados aqui
            args, cwd=cwd, env=env or child_env(), stdout=f, stderr=subprocess.STDOUT, creationflags=flags
        )
        log(f"iniciado {name} (pid {self.procs[name].pid})")

    def stop(self, name: str) -> None:
        p = self.procs.pop(name, None)
        if p is None:
            return
        kill_tree(p.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=15)
        f = self.logs.pop(name, None)
        if f:
            f.close()
        log(f"encerrado {name}")

    def alive(self, name: str) -> bool:
        p = self.procs.get(name)
        return p is not None and p.poll() is None

    def stop_all(self) -> None:
        for name in list(self.procs):
            self.stop(name)


def system_exe(name: str) -> str:
    """Full path of a system tool (never a partial path resolved by the shell)."""
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} não encontrado no PATH")
    return found


def kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - pid numérico
            [system_exe("taskkill"), "/PID", str(pid), "/T", "/F"], capture_output=True, check=False
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(pid), 9)


def kill_pid(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - pid numérico
            [system_exe("taskkill"), "/PID", str(pid), "/F"], capture_output=True, check=False
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)


def go_exe() -> str:
    found = shutil.which("go")
    if found:
        return found
    win = Path(r"C:\Program Files\Go\bin\go.exe")
    if win.exists():
        return str(win)
    raise SystemExit("Go não encontrado no PATH")


def build_binaries() -> Path:
    bindir = WORK / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    flags = "-X github.com/daticopy/dati-monitor/agent/internal/buildinfo.Version=0.0.0-caos"
    for name in ("dm-agent", "dm-watchdog"):
        subprocess.run(  # noqa: S603
            [go_exe(), "build", "-ldflags", flags, "-o", str(bindir / f"{name}{EXE}"), f"./cmd/{name}"],
            cwd=REPO / "agent",
            check=True,
            env=child_env(CGO_ENABLED="0"),
        )
    log("dm-agent e dm-watchdog compilados")
    return bindir


# ----------------------------------------------------------------------------- API


class Portal:
    """Cliente da API como o portal: renova o login quando o token de 15 min expira (o teste dura mais)."""

    def __init__(self, password: str) -> None:
        self.password = password
        self.c = httpx.Client(base_url=API, timeout=30)
        self.login()

    def login(self) -> None:
        self.c.headers.pop("Authorization", None)
        body = {"email": E2E_EMAIL, "password": self.password}
        token = self.ok(self.c.post("/api/v1/auth/login", json=body))
        self.c.headers["Authorization"] = f"Bearer {token['access_token']}"

    @staticmethod
    def ok(resp: httpx.Response) -> Any:
        if resp.is_error:
            raise RuntimeError(
                f"{resp.request.method} {resp.request.url.path} → {resp.status_code}: {resp.text}"
            )
        return resp.json() if resp.content else None

    def call(self, method: str, path: str, **kw: Any) -> Any:
        resp = self.c.request(method, path, **kw)
        if resp.status_code == HTTP_UNAUTHORIZED:
            self.login()
            resp = self.c.request(method, path, **kw)
        return self.ok(resp)

    def get(self, path: str, **params: Any) -> Any:
        return self.call("GET", path, params=params)

    def post(self, path: str, body: Any = None) -> Any:
        return self.call("POST", path, json=body)

    def patch(self, path: str, body: Any) -> Any:
        return self.call("PATCH", path, json=body)

    def delete(self, path: str) -> Any:
        return self.call("DELETE", path)

    def agent(self, agent_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self.get(f"/api/v1/agents/{agent_id}")
        return data

    def command(
        self, agent_id: str, ctype: str, params: dict[str, Any] | None = None, timeout: float = 300
    ) -> dict[str, Any]:
        cmd = self.post(f"/api/v1/agents/{agent_id}/commands", {"type": ctype, "params": params or {}})
        final: dict[str, Any] = {}

        def done() -> bool:
            final.update(self.get(f"/api/v1/commands/{cmd['id']}"))
            return final["state"] in ("succeeded", "failed", "expired", "cancelled")

        wait_until(f"comando {ctype}", done, timeout)
        return final


# ----------------------------------------------------------------------------- banco


def db_url() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _query(sql: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(db_url())
    try:
        return list(await conn.fetch(sql, *args))
    finally:
        await conn.close()


def query(sql: str, *args: Any) -> list[asyncpg.Record]:
    return asyncio.run(_query(sql, *args))


def env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in (REPO / ".env").read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.split(" #", 1)[0].strip()
    return values


async def _terminate_connections() -> int:
    e = env_file()
    dbname = urlsplit(db_url()).path.lstrip("/")
    conn = await asyncpg.connect(
        user=e["POSTGRES_SUPERUSER"],
        password=e["POSTGRES_SUPERUSER_PASSWORD"],
        host=e.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(e.get("POSTGRES_PORT", "5432")),
        database="postgres",
    )
    try:
        rows = await conn.fetch(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            dbname,
        )
        return len(rows)
    finally:
        await conn.close()


def is_admin() -> bool:
    if sys.platform == "win32":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except OSError:
            return False
    return os.geteuid() == 0


# ----------------------------------------------------------------------------- coletores


@dataclass
class Collector:
    name: str
    priority: int
    health: str
    wd_health: str
    agent_id: str = ""

    @property
    def dir(self) -> Path:
        return WORK / self.name

    def info(self) -> dict[str, Any]:
        resp = httpx.get(f"http://{self.health}/health", timeout=3)
        data: dict[str, Any] = resp.json().get("info", {})
        return data


class Chaos:
    def __init__(self, outage_minutes: float) -> None:
        self.outage = outage_minutes
        self.stack = Stack()
        self.report = Report()
        self.a = Collector("coletorA", 10, "127.0.0.1:47811", "127.0.0.1:47812")
        self.b = Collector("coletorB", 50, "127.0.0.1:47821", "127.0.0.1:47822")
        self.portal: Portal | None = None
        self.site_id = ""
        self.started = datetime.now(UTC)
        self.bindir = WORK / "bin"
        self.sims_started = False

    @property
    def p(self) -> Portal:
        if self.portal is None:
            raise RuntimeError("portal ainda não autenticado")
        return self.portal

    # -- ambiente --------------------------------------------------------------------------------------

    def start_server(self) -> None:
        env = child_env(
            PUBLIC_SERVER_URL=API,
            PUBLIC_WS_URL=f"ws://127.0.0.1:{GW_PORT}/ws/agent",
            PYTHONIOENCODING="utf-8",
        )
        backend = REPO / "backend"
        self.stack.start(
            "api",
            [
                str(VENV_PY),
                "-m",
                "uvicorn",
                "app.api.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8100",
            ],
            cwd=backend,
            env=env,
        )
        self.stack.start(
            "gateway",
            [
                str(VENV_PY),
                "-m",
                "uvicorn",
                "app.gateway.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(GW_PORT),
                "--ws",
                "websockets-sansio",
            ],
            cwd=backend,
            env=env,
        )
        ok = wait_until("API e gateway no ar", self.server_up, 90)
        if ok is None:
            raise SystemExit("API/gateway do teste de caos não subiram; veja var/chaos/logs")

    @staticmethod
    def server_up() -> bool:
        return (
            httpx.get(f"{API}/api/health", timeout=3).json().get("status") == "ok"
            and httpx.get(f"http://127.0.0.1:{GW_PORT}/health", timeout=3).status_code == HTTP_OK
        )

    def stop_server(self) -> None:
        self.stack.stop("api")
        self.stack.stop("gateway")

    def prepare(self) -> None:
        if WORK.exists():
            for sub in ("coletorA", "coletorB"):
                shutil.rmtree(WORK / sub, ignore_errors=True)
        WORK.mkdir(parents=True, exist_ok=True)
        self.bindir = build_binaries()
        # Como o dev.ps1: banco no esquema atual antes de subir API, gateway e worker.
        subprocess.run(  # noqa: S603 - comando fixo
            [str(VENV_PY), "-m", "app.cli", "migrate"],
            cwd=REPO / "backend",
            check=True,
            env=child_env(PYTHONIOENCODING="utf-8"),
        )
        log("banco migrado")
        try:
            sims_up = httpx.get(SIM_READY, timeout=2).status_code == HTTP_OK
        except httpx.HTTPError:
            sims_up = False
        if not sims_up:
            self.stack.start("simuladores", [str(VENV_PY), str(REPO / "scripts" / "e2e_sims.py")], cwd=REPO)
            self.sims_started = True
            if (
                wait_until(
                    "simuladores prontos", lambda: httpx.get(SIM_READY, timeout=2).status_code == HTTP_OK, 120
                )
                is None
            ):
                raise SystemExit("simuladores não subiram")
        self.stack.start(
            "worker",
            [str(VENV_PY), "-m", "app.worker.main"],
            cwd=REPO / "backend",
            # Atualização automática desligada: trocar a versão dos coletores no meio mudaria o que se mede.
            env=child_env(PYTHONIOENCODING="utf-8", AUTO_UPDATE="false"),
        )
        self.start_server()
        password = secrets.token_urlsafe(18)
        asyncio.run(ensure_user(password))
        self.portal = Portal(password)
        self.setup_site()
        for c in (self.a, self.b):
            self.enroll(c)
        self.start_watchdog(self.a)
        wait_until("coletor A online", lambda: self.p.agent(self.a.agent_id)["state"] == "online", 90)
        self.start_watchdog(self.b)
        wait_until("coletor B online", lambda: self.p.agent(self.b.agent_id)["state"] == "online", 90)
        vigias = wait_until(
            "vigias com sinal no portal",
            lambda: all(self.p.agent(c.agent_id)["watchdog_alive"] for c in (self.a, self.b)),
            90,
        )
        self.report.check("vigias com sinal no portal (heartbeat do watchdog aceito)", vigias is not None)
        roles = {c.name: self.p.agent(c.agent_id)["cluster_role"] for c in (self.a, self.b)}
        self.report.check(
            "cluster inicial: A MASTER, B STANDBY",
            roles == {"coletorA": "master", "coletorB": "standby"},
            str(roles),
        )
        scan = self.p.command(self.a.agent_id, "scan_now", timeout=240)
        found = (scan.get("result") or {}).get("printers_found")
        self.report.check(
            "varredura inicial encontra as 8 impressoras", found == PRINTERS, f"encontradas {found}"
        )
        read = self.p.command(self.a.agent_id, "read_now", timeout=240)
        self.report.check(
            "leitura inicial concluída", read.get("state") == "succeeded", str(read.get("result"))
        )
        wait_until("fila do coletor A vazia", lambda: self.a.info().get("queue_pending") == 0, 120)

    def setup_site(self) -> None:
        customers = self.p.get("/api/v1/customers", q=CUSTOMER, limit=50)["items"]
        customer = next((c for c in customers if c["name"] == CUSTOMER), None)
        if customer is None:
            companies = self.p.get("/api/v1/companies", limit=500)["items"]
            company = next((c for c in companies if c["legal_name"] == COMPANY), None) or self.p.post(
                "/api/v1/companies", {"legal_name": COMPANY}
            )
            customer = self.p.post("/api/v1/customers", {"name": CUSTOMER, "company_id": company["id"]})
        sites = self.p.get("/api/v1/sites", customer_id=customer["id"], limit=50)["items"]
        site = next((s for s in sites if s["name"] == SITE), None) or self.p.post(
            "/api/v1/sites", {"customer_id": customer["id"], "name": SITE, "timezone": "America/Sao_Paulo"}
        )
        self.site_id = site["id"]
        self.p.patch(
            f"/api/v1/sites/{self.site_id}",
            {
                "collection_config": {
                    "counters_minutes": COUNTERS_MINUTES,
                    "supplies_minutes": 5,
                    "status_minutes": 1,
                }
            },
        )
        for a in self.p.get("/api/v1/agents", site_id=self.site_id, limit=50)["items"]:
            self.p.delete(f"/api/v1/agents/{a['id']}")
        for r in self.p.get(f"/api/v1/sites/{self.site_id}/ip-ranges"):
            self.p.delete(f"/api/v1/ip-ranges/{r['id']}")
        self.p.post(f"/api/v1/sites/{self.site_id}/ip-ranges", {"cidr": "127.0.0.1/32", "ports": SIM_PORTS})

    def enroll(self, c: Collector) -> None:
        created = self.p.post(
            "/api/v1/agents", {"site_id": self.site_id, "name": f"Caos {c.name[-1]}", "priority": c.priority}
        )
        c.agent_id = created["agent"]["id"]
        subprocess.run(  # noqa: S603
            [
                str(self.bindir / f"dm-agent{EXE}"),
                "enroll",
                "--server",
                API,
                "--code",
                created["enrollment"]["code"],
                "--data-dir",
                str(c.dir),
                "--health-addr",
                c.health,
            ],
            check=True,
            env=child_env(),
            capture_output=True,
        )
        log(f"{c.name} cadastrado ({c.agent_id})")

    def agent_exe(self, c: Collector) -> Path:
        """Cada coletor com o próprio executável, como em PCs diferentes (o watchdog troca esse arquivo)."""
        exe = c.dir / "bin" / f"dm-agent{EXE}"
        if not exe.exists():
            exe.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.bindir / f"dm-agent{EXE}", exe)
        return exe

    def start_watchdog(self, c: Collector) -> None:
        self.stack.start(
            f"watchdog-{c.name}",
            [
                str(self.bindir / f"dm-watchdog{EXE}"),
                "run",
                "--data-dir",
                str(c.dir),
                "--agent-exe",
                str(self.agent_exe(c)),
                "--health-addr",
                c.wd_health,
                "--check-every",
                "5s",
                "--report-every",
                "15s",
                "--start-grace",
                "20s",
            ],
            cwd=REPO,
        )

    # -- cenários --------------------------------------------------------------------------------------

    def kill_agent_process(self) -> None:
        log("== 1. morte do processo do coletor A")
        pid = int(self.a.info()["pid"])
        kill_pid(pid)
        took = wait_until(
            "coletor A reiniciado pelo watchdog",
            lambda: int(self.a.info().get("pid", 0)) not in (0, pid),
            90,
        )
        self.report.check(
            "watchdog reinicia o coletor morto", took is not None, f"{took:.0f} s" if took else "não voltou"
        )

        def reason_reported() -> bool:
            restarts = self.p.agent(self.a.agent_id)["watchdog_status"].get("restarts", [])
            return any("parado" in r.get("reason", "") for r in restarts)

        self.report.check(
            "motivo do reinício chega ao portal",
            wait_until("motivo no portal", reason_reported, 90) is not None,
        )
        back = wait_until(
            "coletor A online no portal", lambda: self.p.agent(self.a.agent_id)["state"] == "online", 120
        )
        self.report.check("coletor A volta a online", back is not None)

    def internet_down(self) -> None:
        log(f"== 2. queda de internet: API e gateway fora por {self.outage:g} min")
        t0 = datetime.now(UTC)
        self.stop_server()
        max_queue, restarts_during = 0, 0
        pid_a = int(self.a.info().get("pid", 0))
        end = time.monotonic() + self.outage * 60
        while time.monotonic() < end:
            time.sleep(30)
            with contextlib.suppress(httpx.HTTPError, OSError, ValueError):
                info = self.a.info()
                max_queue = max(max_queue, int(info.get("queue_pending", 0)))
                if int(info.get("pid", pid_a)) != pid_a:
                    restarts_during += 1
                    pid_a = int(info["pid"])
            log(f"   fila local do coletor A: {max_queue} (máximo até agora)")
        self.start_server()
        t1 = datetime.now(UTC)
        self.report.check(
            "leituras ficam na fila local durante a queda", max_queue > 0, f"máximo {max_queue} itens"
        )
        self.report.check(
            "coletor não é reiniciado à toa durante a queda (o /health continua saudável)",
            restarts_during == 0,
            f"{restarts_during} reinício(s)",
        )
        drained = wait_until(
            "filas zeradas depois da volta",
            lambda: all(c.info().get("queue_pending") == 0 for c in (self.a, self.b)),
            300,
        )
        self.report.check(
            "fila local esvazia sozinha quando o servidor volta",
            drained is not None,
            f"{drained:.0f} s" if drained else "",
        )
        online = wait_until(
            "coletores online de novo",
            lambda: all(self.p.agent(c.agent_id)["state"] == "online" for c in (self.a, self.b)),
            180,
        )
        self.report.check("coletores voltam a online sem intervenção", online is not None)
        rows = query(
            """SELECT d.serial, count(r.id) AS n FROM devices d
               LEFT JOIN readings r ON r.device_id = d.id AND r.read_at > $2 AND r.read_at < $3
               WHERE d.site_id = $1 AND d.deleted_at IS NULL GROUP BY d.serial ORDER BY d.serial""",
            uuid.UUID(self.site_id),
            t0,
            t1,
        )
        missing = [r["serial"] for r in rows if r["n"] == 0]
        self.report.check(
            "leituras feitas durante a queda chegaram ao banco (nenhuma perdida)",
            len(rows) == PRINTERS and not missing,
            ", ".join(f"{r['serial']}={r['n']}" for r in rows),
        )

    def postgres_restart(self) -> None:
        log("== 3. PostgreSQL")
        if is_admin():
            subprocess.run(  # noqa: S603 - comando fixo
                [
                    system_exe("powershell"),
                    "-NoProfile",
                    "-Command",
                    "Restart-Service -Name postgresql-x64-16 -Force",
                ],
                check=True,
            )
            how = "serviço postgresql-x64-16 reiniciado"
        else:
            n = asyncio.run(_terminate_connections())
            how = f"sem administrador: {n} conexão(ões) do banco derrubadas (pg_terminate_backend)"
        log(f"   {how}")
        api_ok = wait_until(
            "API com banco ok",
            lambda: httpx.get(f"{API}/api/health", timeout=3).json().get("status") == "ok",
            90,
        )
        self.report.check(f"API se recupera do banco ({how})", api_ok is not None)
        cmd = self.p.command(self.a.agent_id, "reconnect", timeout=120)
        self.report.check(
            "comando ao vivo depois do banco (LISTEN do gateway reconectou)",
            cmd.get("state") == "succeeded",
            str(cmd.get("state")),
        )
        before = self.p.agent(self.b.agent_id)["last_seen_at"]
        moved = wait_until(
            "heartbeat depois do banco", lambda: self.p.agent(self.b.agent_id)["last_seen_at"] != before, 90
        )
        self.report.check("heartbeats continuam gravando depois do banco", moved is not None)

    def kill_master(self) -> None:
        log("== 4. queda do PC do MASTER (coletor A e watchdog A mortos)")
        self.stack.stop(f"watchdog-{self.a.name}")  # mata a árvore: watchdog e o coletor filho
        t_kill = datetime.now(UTC)

        def b_master() -> bool:
            return bool(self.p.agent(self.b.agent_id)["cluster_role"] == "master")

        took = wait_until("B assume como MASTER", b_master, 420, every=5)
        self.report.check(
            "STANDBY assume quando o lease do MASTER expira",
            took is not None,
            f"{took:.0f} s depois da queda" if took else "não assumiu",
        )
        site = uuid.UUID(self.site_id)
        b_id = uuid.UUID(self.b.agent_id)

        def b_reads_all() -> bool:
            rows = query(
                "SELECT count(DISTINCT r.device_id) FROM readings r JOIN devices d ON d.id = r.device_id "
                "WHERE d.site_id = $1 AND r.agent_id = $2 AND r.read_at > $3",
                site,
                b_id,
                t_kill,
            )
            return int(rows[0][0]) == PRINTERS

        reads = wait_until("B lê as 8 impressoras", b_reads_all, 480, every=10)
        self.report.check("novo MASTER varre e lê as 8 impressoras", reads is not None)
        self.start_watchdog(self.a)
        back = wait_until("A volta", lambda: self.p.agent(self.a.agent_id)["state"] == "online", 180)
        roles = {c.name: self.p.agent(c.agent_id)["cluster_role"] for c in (self.a, self.b)}
        self.report.check(
            "antigo MASTER volta como STANDBY (não retoma sozinho)",
            back is not None and roles == {"coletorA": "standby", "coletorB": "master"},
            str(roles),
        )

    def final_checks(self) -> None:
        log("== verificação final")
        drained = wait_until(
            "filas zeradas", lambda: all(c.info().get("queue_pending") == 0 for c in (self.a, self.b)), 300
        )
        dead = {c.name: c.info().get("queue_dead") for c in (self.a, self.b)}
        self.report.check(
            "nenhum item em dead-letter nos coletores",
            drained is not None and all(v == 0 for v in dead.values()),
            str(dead),
        )
        half = timedelta(minutes=COUNTERS_MINUTES) / 2
        dups = query(
            """SELECT d.serial, r1.read_at AS a, r2.read_at AS b FROM readings r1
               JOIN readings r2 ON r2.device_id = r1.device_id AND r2.id <> r1.id
                 AND r2.agent_id <> r1.agent_id
                 AND r2.read_at >= r1.read_at AND r2.read_at - r1.read_at < $3
               JOIN devices d ON d.id = r1.device_id
               WHERE d.site_id = $1 AND r1.read_at > $2 AND r2.read_at > $2""",
            uuid.UUID(self.site_id),
            self.started,
            half,
        )
        self.report.check(
            "sem leituras duplicadas entre coletores (anti-duplicidade)",
            not dups,
            f"{len(dups)} par(es)" + (f": {dups[0]['serial']}" if dups else ""),
        )
        online = all(self.p.agent(c.agent_id)["state"] == "online" for c in (self.a, self.b))
        self.report.check("tudo online no fim, sem intervenção manual", online)
        vigias = wait_until(
            "vigias com sinal no fim",
            lambda: all(self.p.agent(c.agent_id)["watchdog_alive"] for c in (self.a, self.b)),
            90,
        )
        self.report.check("vigias com sinal no fim", vigias is not None)

    def run(self) -> int:
        try:
            self.prepare()
            self.kill_agent_process()
            self.internet_down()
            self.postgres_restart()
            self.kill_master()
            self.final_checks()
        except Exception as exc:  # noqa: BLE001 - o relatório precisa registrar qualquer falha
            self.report.check("execução do teste de caos", False, f"{type(exc).__name__}: {exc}")
        finally:
            self.stack.stop_all()
        out = {
            "inicio": self.started.isoformat(),
            "fim": datetime.now(UTC).isoformat(),
            "queda_minutos": self.outage,
            "administrador": is_admin(),
            "itens": [{"item": n, "ok": ok, "detalhe": d} for n, ok, d in self.report.items],
        }
        (WORK / "relatorio.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        failed = self.report.failed()
        log(
            f"RESULTADO: {len(self.report.items) - len(failed)} OK, {len(failed)} FALHOU "
            "(relatório em var/chaos/relatorio.json)"
        )
        return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--outage-minutes", type=float, default=10.0, help="duração da queda da API/gateway")
    args = parser.parse_args(argv)
    if get_settings().app_env == "production":
        raise SystemExit("o teste de caos é só para desenvolvimento")
    return Chaos(args.outage_minutes).run()


if __name__ == "__main__":
    raise SystemExit(main())
