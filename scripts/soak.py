"""Teste de resistência do coletor (PROMPT 13): dm-agent real rodando contra o servidor e os simuladores do
scripts\\dev.ps1 por N minutos, medindo memória (RSS), goroutines, handles abertos e a fila local pelo
/health. Falha se, depois de estabilizar, a memória crescer mais de 20% (goroutines/handles com a mesma
regra e uma folga pequena), se a fila não esvaziar ou se o agente parar.

Só desenvolvimento/CI (recusa APP_ENV=production). Cria um local próprio ("Local soak ...", ativação
automática, faixa 127.0.0.1 portas 1161 a 1168) e um coletor novo; pede "Ler agora" a cada minuto para
manter o agente trabalhando.

Uso:  .venv\\Scripts\\python scripts\\soak.py --minutes 30 [--api http://127.0.0.1:8000]
      (o scripts\\soak.ps1 chama este script; 24 h: --minutes 1440)
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "var" / "soak"
HEALTH_ADDR = "127.0.0.1:47795"
SIM_PORTS = list(range(1161, 1169))
GROWTH_LIMIT = 0.20
# Usuário próprio: o e2e_seed troca a senha do usuário que recebe, e outra execução dele (E2E, roteiro
# manual) no meio do soak revogaria a sessão.
SOAK_EMAIL = "soak@dati.local"
HTTP_UNAUTHORIZED = 401


@dataclass
class Sample:
    t: float
    private: int
    go_sys: int
    rss: int
    goroutines: int
    handles: int
    heap: int
    queue: int
    status: str


def fail(msg: str) -> None:
    sys.stderr.write(f"ERRO: {msg}\n")
    raise SystemExit(1)


def log(msg: str) -> None:
    sys.stdout.write(f"{datetime.now():%H:%M:%S} {msg}\n")
    sys.stdout.flush()


def go_exe() -> str:
    win = Path(r"C:\Program Files\Go\bin\go.exe")
    return str(win) if win.exists() else "go"


class Portal:
    """Cliente do portal que entra de novo quando o token de acesso (15 min) vence."""

    def __init__(self, api: str, email: str, password: str) -> None:
        self.client = httpx.Client(base_url=api, timeout=30)
        self.email, self.password = email, password
        self.login()

    def login(self) -> None:
        tok = self.client.post("/api/v1/auth/login", json={"email": self.email, "password": self.password})
        tok.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {tok.json()['access_token']}"

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        r = self.client.request(method, url, **kw)
        if r.status_code == HTTP_UNAUTHORIZED:
            self.login()
            r = self.client.request(method, url, **kw)
        return r

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> httpx.Response:
        return self.request("POST", url, **kw)


def setup(api: str) -> tuple[Portal, str]:
    """Usuário próprio do soak (seed), local com a faixa dos simuladores e um coletor novo."""
    password = "Soak-" + secrets.token_urlsafe(12)
    seed = subprocess.run(  # noqa: S603 - script do próprio repositório
        [sys.executable, str(REPO / "scripts" / "e2e_seed.py"), "--api", api, "--email", SOAK_EMAIL],
        env={**os.environ, "DM_E2E_PASSWORD": password, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if seed.returncode != 0:
        fail(f"e2e_seed falhou: {seed.stderr}")
    info = json.loads(seed.stdout.strip().splitlines()[-1])
    c = Portal(api, info["email"], password)
    site_ref = c.get(f"/api/v1/sites/{info['real_site_id']}")
    site_ref.raise_for_status()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    site = c.post(
        "/api/v1/sites",
        json={
            "customer_id": site_ref.json()["customer_id"],
            "name": f"Local soak {stamp}",
            "auto_activate_devices": True,
        },
    )
    site.raise_for_status()
    site_id = site.json()["id"]
    rng = c.post(f"/api/v1/sites/{site_id}/ip-ranges", json={"host": "127.0.0.1", "ports": SIM_PORTS})
    rng.raise_for_status()
    agent = c.post("/api/v1/agents", json={"site_id": site_id, "name": f"Coletor soak {stamp}"})
    agent.raise_for_status()
    return c, json.dumps(
        {"agent_id": agent.json()["agent"]["id"], "code": agent.json()["enrollment"]["code"]}
    )


def health(hc: httpx.Client) -> dict[str, Any] | None:
    try:
        r = hc.get(f"http://{HEALTH_ADDR}/health", timeout=5)
        data: dict[str, Any] = r.json()
        return data
    except (httpx.HTTPError, ValueError):
        return None


def growth(base: float, end: float) -> float:
    return 0.0 if base <= 0 else (end - base) / base


def evaluate(samples: list[Sample]) -> list[str]:
    """Baseline = média logo depois do aquecimento; fim = média dos últimos 20% das amostras."""
    problems = []
    n = len(samples)
    if n < 10:  # noqa: PLR2004
        return [f"poucas amostras ({n}); rode pelo menos 5 minutos"]
    warm = max(int(n * 0.2), 1)
    window = max(int(n * 0.2), 1)
    base, end = samples[warm : warm + window], samples[-window:]

    def avg(rows: list[Sample], field: str) -> float:
        return statistics.fmean(getattr(r, field) for r in rows)

    # Memória = bytes privados do processo (Windows "Private Bytes"; Linux RssAnon) e a memória que o
    # runtime do Go pediu ao SO. O working set (rss) fica no relatório, mas não reprova: no Windows ele
    # inclui páginas compartilhadas e mapeadas (DLLs, arquivos do SQLite) e sobe ou desce conforme o SO
    # gerencia a memória da máquina, mesmo com o coletor parado (visto no soak de 02/10: 23 → 66 → 34 MB
    # com heap, memória privada e goroutines estáveis).
    for field, label, slack in (
        ("private", "memória privada", 0),
        ("go_sys", "memória do runtime Go", 0),
        ("goroutines", "goroutines", 5),
        ("handles", "handles", 10),
    ):
        b, e = avg(base, field), avg(end, field)
        if e > b * (1 + GROWTH_LIMIT) + slack:
            problems.append(f"{label} cresceu {growth(b, e):.0%} (de {b:,.0f} para {e:,.0f})")
    if samples[-1].queue > 1000:  # noqa: PLR2004
        problems.append(f"fila local não esvaziou ({samples[-1].queue} itens no fim)")
    down = [s for s in samples if s.status != "ok"]
    if down:
        problems.append(f"/health não saudável em {len(down)} amostra(s)")
    return problems


def prepare(api_url: str, work: Path) -> tuple[Path, Portal, dict[str, str]]:
    """Compila o dm-agent, cria local/coletor e cadastra o agente na pasta de trabalho."""
    exe = work / ("dm-agent.exe" if os.name == "nt" else "dm-agent")
    build = subprocess.run(  # noqa: S603
        [go_exe(), "build", "-o", str(exe), "./cmd/dm-agent"],
        cwd=REPO / "agent",
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        fail(f"go build falhou: {build.stderr}")
    api, ids = setup(api_url)
    meta: dict[str, str] = json.loads(ids)
    cmd = [str(exe), "enroll", "--server", api_url, "--code", meta["code"]]
    cmd += ["--data-dir", str(work / "data"), "--health-addr", HEALTH_ADDR]
    enroll = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=False)  # noqa: S603
    if enroll.returncode != 0:
        fail(f"cadastro do coletor falhou: {enroll.stderr}")
    return exe, api, meta


def sample(hc: httpx.Client, elapsed: float) -> Sample | None:
    h = health(hc)
    if h is None or "runtime" not in (h.get("info") or {}):
        return None
    rt = h["info"]["runtime"]
    return Sample(
        t=round(elapsed, 1),
        private=int(rt["private_bytes"]),
        go_sys=int(rt["sys_bytes"]),
        rss=int(rt.get("rss_bytes", 0)),
        goroutines=int(rt["goroutines"]),
        handles=int(rt.get("open_handles", 0)),
        heap=int(rt["heap_alloc_bytes"]),
        queue=int(h["info"].get("queue_pending", 0)),
        status=h["status"],
    )


def run(
    proc: subprocess.Popen[bytes], api: Portal, agent_id: str, args: argparse.Namespace, work: Path
) -> list[Sample]:
    samples: list[Sample] = []
    started = time.monotonic()
    last_read = 0.0
    hc = httpx.Client()
    while time.monotonic() - started < args.minutes * 60:
        if proc.poll() is not None:
            fail(f"o agente parou (código {proc.returncode}); veja {work / 'agent.log'}")
        now = time.monotonic()
        if now - last_read >= 60:  # noqa: PLR2004 - "Ler agora" a cada minuto
            r = api.post(f"/api/v1/agents/{agent_id}/commands", json={"type": "read_now", "params": {}})
            if r.status_code >= 400:  # noqa: PLR2004
                log(f"AVISO: 'Ler agora' recusado: {r.status_code} {r.text[:200]}")
            last_read = now
        s = sample(hc, now - started)
        if s is not None:
            samples.append(s)
            mb = 1048576
            log(
                f"privada={s.private / mb:.1f} MB go={s.go_sys / mb:.1f} MB ws={s.rss / mb:.1f} MB "
                f"goroutines={s.goroutines} handles={s.handles} fila={s.queue} {s.status}"
            )
        time.sleep(args.every)
    return samples


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--every", type=float, default=30, help="segundos entre amostras")
    args = ap.parse_args()
    # Console do Windows em cp1252: sem isto, "→" e acentos derrubariam o relatório no fim.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if os.environ.get("APP_ENV") == "production":
        fail("teste de resistência só para desenvolvimento/CI")
    work = OUT / datetime.now().strftime("%Y%m%d-%H%M%S")
    work.mkdir(parents=True)
    exe, api, meta = prepare(args.api, work)
    env = {k: v for k, v in os.environ.items() if k != "__COMPAT_LAYER"}
    log(f"soak de {args.minutes:g} min; coletor {meta['agent_id']}; pasta {work}")
    with (work / "agent.log").open("w", encoding="utf-8") as agent_log:
        proc = subprocess.Popen(  # noqa: S603
            [str(exe), "run", "--data-dir", str(work / "data")],
            stdout=agent_log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:
            samples = run(proc, api, meta["agent_id"], args, work)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    problems = evaluate(samples)
    report = {
        "minutes": args.minutes,
        "finished_at": datetime.now(UTC).isoformat(),
        "agent_id": meta["agent_id"],
        "samples": [asdict(s) for s in samples],
        "problems": problems,
        "ok": not problems,
    }
    (work / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if samples:
        a, b = samples[0], samples[-1]
        log(
            f"memória privada {a.private / 1048576:.1f} → {b.private / 1048576:.1f} MB; "
            f"goroutines {a.goroutines} → {b.goroutines}"
        )
        log(f"handles {a.handles} → {b.handles}; fila no fim {b.queue}")
    for p in problems:
        sys.stderr.write(f"FALHOU: {p}\n")
    if problems:
        return 1
    log(f"OK: soak de {args.minutes:g} min sem crescimento (relatório {work / 'report.json'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
