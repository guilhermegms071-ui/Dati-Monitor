"""Aceitação final (PROMPT seção 15): sobe tudo do zero e verifica cada critério de pronto, com um relatório
OK/FALHOU por item. Cada item aponta para as provas que o sustentam (testes Go/pytest/Playwright pelo
nome, checagens do teste de caos, relatórios do soak e da carga); prova ausente conta como falha.

Etapas (na ordem; ~3 h com os padrões):
  1. dev.ps1 do zero → portal, API, login do administrador do teste; instalador (verificações e, como
     administrador, a instalação completa); soak de 30 min com o dev no ar;
  2. dev parado → lint, Go (-race, integração), pytest (cobertura), Vitest, Playwright, caos (queda de
     internet de 60 min) e carga (ambiente próprio).

Uso:  scripts\\acceptance.ps1                       (ou .venv\\Scripts\\python scripts\\acceptance.py)
      --skip soak,load,chaos   pula etapas (os itens que dependem delas ficam FALHOU: "não executado")
Só desenvolvimento/CI: recusa APP_ENV=production. Senhas de teste só em memória.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
VENV_PY = REPO / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
API, GATEWAY, PORTAL = "http://127.0.0.1:8000", "http://127.0.0.1:8001", "http://localhost:5173"
ADMIN_EMAIL = "aceite@dati.local"
STEPS = ("dev", "installer", "soak", "lint", "go", "pytest", "vitest", "e2e", "chaos", "load")
HTTP_OK = 200
# Exigências da seção 15 (o relatório reprova execuções mais curtas que isto).
SOAK_MINUTES_REQUIRED = 30
LOAD_DEVICES_REQUIRED = 20_000
OUTAGE_MINUTES_REQUIRED = 60
COVERAGE_REQUIRED = 80
LOG_CMD_WORDS = 4


def log(msg: str) -> None:
    sys.stdout.write(f"{datetime.now():%H:%M:%S} {msg}\n")
    sys.stdout.flush()


def env() -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if k != "__COMPAT_LAYER"}
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def powershell() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0  # type: ignore[attr-defined,unused-ignore]
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined,unused-ignore]
    except OSError:
        return False


@dataclass
class Step:
    ok: bool
    detail: str = ""
    seconds: float = 0


@dataclass
class Evidence:
    """Resultados coletados: etapas e testes individuais por suíte."""

    steps: dict[str, Step] = field(default_factory=dict)
    go: dict[str, str] = field(default_factory=dict)  # Test → pass/fail/skip
    py: dict[str, str] = field(default_factory=dict)  # função (e arquivo::função) → passed/failed/skipped
    e2e: dict[str, str] = field(default_factory=dict)  # título → passed/failed/...
    chaos: dict[str, tuple[bool, str]] = field(default_factory=dict)
    chaos_outage: float = 0
    soak: dict[str, Any] = field(default_factory=dict)
    load: dict[str, Any] = field(default_factory=dict)


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.skip = {s.strip() for s in args.skip.split(",") if s.strip()}
        self.work = REPO / "var" / "acceptance" / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.work.mkdir(parents=True)
        self.ev = Evidence()
        self.password = "Aceite-" + secrets.token_urlsafe(16)

    # ------------------------------------------------------------------------- utilidades

    def run(self, name: str, cmd: list[str], cwd: Path = REPO, timeout: float = 4 * 3600) -> int:
        """Roda um comando com a saída num log próprio; devolve o código de saída."""
        path = self.work / f"{name}.log"
        log(f"→ {name}: {' '.join(cmd[:4])}{' …' if len(cmd) > LOG_CMD_WORDS else ''} (log em {path.name})")
        with path.open("w", encoding="utf-8") as out:
            try:
                return subprocess.run(  # noqa: S603 - comandos do próprio repositório
                    cmd,
                    cwd=cwd,
                    env=env(),
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                ).returncode
            except subprocess.TimeoutExpired:
                out.write(f"\nTEMPO ESGOTADO ({timeout:.0f} s)\n")
                return 124

    def step(self, name: str, fn: Callable[[], Step]) -> None:
        if name in self.skip:
            self.ev.steps[name] = Step(False, "não executado (--skip)")
            log(f"-- {name}: pulado")
            return
        t0 = time.monotonic()
        try:
            st = fn()
        except Exception as exc:  # noqa: BLE001 - qualquer falha de uma etapa vai para o relatório
            st = Step(False, f"{type(exc).__name__}: {exc}")
        st.seconds = time.monotonic() - t0
        self.ev.steps[name] = st
        log(f"{'OK     ' if st.ok else 'FALHOU '} etapa {name} ({st.seconds / 60:.1f} min) {st.detail}")

    def ps(self, name: str, script: str, *args: str, timeout: float = 4 * 3600) -> int:
        cmd = [
            powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO / "scripts" / script),
        ]
        return self.run(name, [*cmd, *args], timeout=timeout)

    # ------------------------------------------------------------------------- ambiente de desenvolvimento

    def dev_start(self) -> None:
        self.dev_stop()
        script = str(REPO / "scripts" / "dev.ps1")
        launch = (
            f"Start-Process {powershell()} -WindowStyle Hidden -WorkingDirectory '{REPO}' "
            f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','{script}'"
        )
        subprocess.run([powershell(), "-NoProfile", "-Command", launch], env=env(), check=True)  # noqa: S603
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.dev_up():
                return
            time.sleep(3)
        raise RuntimeError("dev.ps1 não subiu API, gateway e portal em 5 min (veja var/log)")

    def dev_stop(self) -> None:
        self.ps("stop-dev", "stop-dev.ps1", timeout=120)

    @staticmethod
    def dev_up() -> bool:
        try:
            api = httpx.get(f"{API}/api/health", timeout=5).json()
            gw = httpx.get(f"{GATEWAY}/health", timeout=5).status_code
            portal = httpx.get(PORTAL, timeout=5).status_code
        except (httpx.HTTPError, ValueError):
            return False
        return api.get("status") == "ok" and gw == HTTP_OK and portal == HTTP_OK

    # ------------------------------------------------------------------------- etapas

    def step_dev(self) -> Step:
        self.dev_start()
        if self._seed() != 0:
            return Step(False, "seed do administrador do teste falhou")
        resp = httpx.post(
            f"{API}/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": self.password}, timeout=30
        )
        if resp.status_code != HTTP_OK:
            return Step(False, f"login do administrador recusado: HTTP {resp.status_code}")
        me = httpx.get(
            f"{API}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
            timeout=30,
        ).json()
        portal = httpx.get(PORTAL, timeout=10)
        ok = (
            portal.status_code == HTTP_OK
            and '<div id="root"' in portal.text
            and me.get("email") == ADMIN_EMAIL
        )
        return Step(
            ok, f"portal {portal.status_code}, API e banco ok, login de {me.get('email')} ({me.get('role')})"
        )

    def _seed(self) -> int:
        path = self.work / "seed-admin.log"
        with path.open("w", encoding="utf-8") as out:
            return subprocess.run(  # noqa: S603
                [str(VENV_PY), "scripts/e2e_seed.py", "--email", ADMIN_EMAIL],
                cwd=REPO,
                env={**env(), "DM_E2E_PASSWORD": self.password},
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=300,
                check=False,
            ).returncode

    def enrollment_code(self) -> str:
        r = subprocess.run(  # noqa: S603
            [str(VENV_PY), "scripts/ci_enrollment_code.py"],
            cwd=REPO,
            env=env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(f"código de cadastro: {r.stderr[-500:]}")
        return r.stdout.strip().splitlines()[-1]

    def step_installer(self) -> Step:
        code = self.enrollment_code()
        checks = self.ps(
            "installer-checks", "test-installer.ps1", "-Server", API, "-Code", code, timeout=1800
        )
        self.ev.steps["installer_checks"] = Step(checks == 0, "verificações sem administrador")
        if not is_admin():
            self.ev.steps["installer_full"] = Step(
                False,
                "instalação completa precisa de terminal COMO ADMINISTRADOR "
                "(ou do job windows-installer do CI)",
            )
            return Step(checks == 0, "verificações ok; instalação completa não executada (sem administrador)")
        full = self.ps(
            "installer-full", "test-installer.ps1", "-Server", API, "-Code", self.enrollment_code(), "-Full"
        )
        self.ev.steps["installer_full"] = Step(
            full == 0, "serviços, recuperação, atualização, rollback, remoção"
        )
        return Step(checks == 0 and full == 0, "verificações e instalação completa")

    def step_soak(self) -> Step:
        before = (
            set((REPO / "var" / "soak").glob("*/report.json")) if (REPO / "var" / "soak").exists() else set()
        )
        code = self.run("soak", [str(VENV_PY), "scripts/soak.py", "--minutes", str(self.args.soak_minutes)])
        new = sorted(set((REPO / "var" / "soak").glob("*/report.json")) - before)
        if new:
            self.ev.soak = json.loads(new[-1].read_text(encoding="utf-8"))
        return Step(code == 0 and bool(self.ev.soak.get("ok")), "; ".join(self.ev.soak.get("problems", [])))

    def step_lint(self) -> Step:
        return Step(self.ps("lint", "lint.ps1", timeout=3600) == 0)

    def step_go(self) -> Step:
        gcc_env = env()
        gcc_env["CGO_ENABLED"] = "1"
        if not shutil.which("gcc"):
            base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
            gcc = next(iter(base.glob("BrechtSanders.WinLibs*/**/gcc.exe")), None)
            if gcc is None:
                return Step(False, "gcc não encontrado (go test -race precisa dele)")
            gcc_env["PATH"] = f"{gcc.parent}{os.pathsep}{gcc_env.get('PATH', '')}"
        go = shutil.which("go") or r"C:\Program Files\Go\bin\go.exe"
        cover = self.work / "go-cover.out"
        out = self.work / "go.jsonl"
        log("→ go test -race -tags integration (json)")
        with out.open("w", encoding="utf-8") as f:
            code = subprocess.run(  # noqa: S603
                [
                    go,
                    "test",
                    "-race",
                    "-count=1",
                    "-json",
                    "-tags",
                    "integration",
                    f"-coverprofile={cover}",
                    "-coverpkg=./internal/...",
                    "./...",
                ],
                cwd=REPO / "agent",
                env=gcc_env,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=3600,
                check=False,
            ).returncode
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("Test") and e.get("Action") in ("pass", "fail", "skip"):
                self.ev.go[e["Test"]] = e["Action"]
        pct = self.go_coverage(go, cover)
        failed = sorted(t for t, a in self.ev.go.items() if a == "fail")
        ok = code == 0 and not failed and pct >= COVERAGE_REQUIRED
        return Step(
            ok,
            f"{len(self.ev.go)} testes, cobertura internal/ {pct:.1f}%"
            + (f"; falharam {failed[:5]}" if failed else ""),
        )

    def go_coverage(self, go: str, cover: Path) -> float:
        if not cover.exists():
            return 0.0
        r = subprocess.run(  # noqa: S603
            [go, "tool", "cover", f"-func={cover}"],
            cwd=REPO / "agent",
            capture_output=True,
            text=True,
            check=False,
        )
        last = (r.stdout.strip().splitlines() or ["0"])[-1]
        try:
            return float(last.split()[-1].rstrip("%"))
        except ValueError:
            return 0.0

    def step_pytest(self) -> Step:
        xml = self.work / "pytest.xml"
        code = self.run(
            "pytest",
            [
                str(VENV_PY),
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--junitxml={xml}",
                "--cov=app",
                "--cov-fail-under=80",
            ],
            cwd=REPO / "backend",
        )
        if xml.exists():
            for case in ET.parse(xml).getroot().iter("testcase"):  # noqa: S314
                state = "passed"
                if case.find("failure") is not None or case.find("error") is not None:
                    state = "failed"
                elif case.find("skipped") is not None:
                    state = "skipped"
                name = case.get("name", "")
                module = case.get("classname", "").rsplit(".", 1)[-1]
                self.ev.py[name] = state
                self.ev.py[f"{module}::{name}"] = state
        failed = sorted(k for k, v in self.ev.py.items() if v == "failed" and "::" in k)
        return Step(
            code == 0 and not failed, f"{sum('::' in k for k in self.ev.py)} testes, cobertura >= 80%"
        )

    def step_vitest(self) -> Step:
        npx = shutil.which("npx") or "npx"
        return Step(self.run("vitest", [npx, "vitest", "run"], cwd=REPO / "frontend") == 0)

    def step_e2e(self) -> Step:
        npx = shutil.which("npx") or "npx"
        out = self.work / "playwright.json"
        e = env()
        e["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(out)
        log("→ playwright test --reporter=json")
        with (self.work / "e2e.log").open("w", encoding="utf-8") as f:
            code = subprocess.run(  # noqa: S603
                [npx, "playwright", "test", "--reporter=json"],
                cwd=REPO / "frontend",
                env=e,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=3600,
                check=False,
            ).returncode

        def walk(suite: dict[str, Any]) -> None:
            for spec in suite.get("specs", []):
                results = [r.get("status") for t in spec.get("tests", []) for r in t.get("results", [])]
                self.ev.e2e[spec["title"]] = "passed" if spec.get("ok") and results else "failed"
            for child in suite.get("suites", []):
                walk(child)

        if out.exists():
            for suite in json.loads(out.read_text(encoding="utf-8")).get("suites", []):
                walk(suite)
        failed = [t for t, s in self.ev.e2e.items() if s != "passed"]
        return Step(
            code == 0 and bool(self.ev.e2e) and not failed,
            f"{len(self.ev.e2e)} testes" + (f"; falharam {failed}" if failed else ""),
        )

    def step_chaos(self) -> Step:
        report = REPO / "var" / "chaos" / "relatorio.json"
        report.unlink(missing_ok=True)
        code = self.run(
            "chaos", [str(VENV_PY), "scripts/chaos.py", "--outage-minutes", str(self.args.outage_minutes)]
        )
        if report.exists():
            data = json.loads(report.read_text(encoding="utf-8"))
            self.ev.chaos_outage = float(data.get("queda_minutos", 0))
            self.ev.chaos = {i["item"]: (bool(i["ok"]), i.get("detalhe", "")) for i in data.get("itens", [])}
        failed = [n for n, (ok, _) in self.ev.chaos.items() if not ok]
        return Step(
            code == 0 and bool(self.ev.chaos) and not failed,
            f"queda de {self.ev.chaos_outage:g} min" + (f"; falharam {failed}" if failed else ""),
        )

    def step_load(self) -> Step:
        before = (
            set((REPO / "var" / "load").glob("*/report.json")) if (REPO / "var" / "load").exists() else set()
        )
        code = self.run("load", [str(VENV_PY), "scripts/load.py"])
        new = sorted(set((REPO / "var" / "load").glob("*/report.json")) - before)
        if new:
            self.ev.load = json.loads(new[-1].read_text(encoding="utf-8"))
        return Step(code == 0 and bool(self.ev.load.get("ok")), "; ".join(self.ev.load.get("problems", [])))

    # ------------------------------------------------------------------------- execução

    def execute(self) -> None:
        log(f"aceitação — relatório em {self.work}")
        self.step("dev", self.step_dev)
        if self.ev.steps["dev"].ok:
            self.step("installer", self.step_installer)
            self.step("soak", self.step_soak)
        else:
            for s in ("installer", "soak"):
                self.ev.steps[s] = Step(False, "dev.ps1 não subiu")
        self.dev_stop()  # E2E e caos sobem os próprios servidores nas mesmas portas/banco
        for name, fn in (
            ("lint", self.step_lint),
            ("go", self.step_go),
            ("pytest", self.step_pytest),
            ("vitest", self.step_vitest),
            ("e2e", self.step_e2e),
            ("chaos", self.step_chaos),
            ("load", self.step_load),
        ):
            self.step(name, fn)


# ----------------------------------------------------------------------------- critérios da seção 15


class Judge:
    def __init__(self, ev: Evidence) -> None:
        self.ev = ev
        self.lines: list[str] = []
        self.ok = True

    def step(self, name: str) -> bool:
        st = self.ev.steps.get(name)
        self.lines.append(
            f"etapa {name}: {'ok' if st and st.ok else 'FALHOU'} {st.detail if st else 'não executada'}"
        )
        return bool(st and st.ok)

    def go(self, test: str) -> bool:
        state = self.ev.go.get(test, "ausente")
        self.lines.append(f"Go {test}: {state}")
        return state == "pass"

    def py(self, test: str) -> bool:
        state = self.ev.py.get(test, "ausente")
        self.lines.append(f"pytest {test}: {state}")
        return state == "passed"

    def py_module(self, module: str) -> bool:
        states = [v for k, v in self.ev.py.items() if k.startswith(f"{module}::")]
        self.lines.append(f"pytest {module}: {len(states)} testes, {states.count('passed')} ok")
        return bool(states) and all(s == "passed" for s in states)

    def e2e(self, title_part: str) -> bool:
        hits = {t: s for t, s in self.ev.e2e.items() if title_part in t}
        self.lines.append(f"E2E '{title_part}': {', '.join(hits.values()) or 'ausente'}")
        return bool(hits) and all(s == "passed" for s in hits.values())

    def chaos(self, check_name: str) -> bool:
        ok, detail = self.ev.chaos.get(check_name, (False, "ausente"))
        self.lines.append(f"caos '{check_name}': {'ok' if ok else 'FALHOU'} {detail}")
        return ok

    def fact(self, what: str, ok: bool) -> bool:
        self.lines.append(f"{what}: {'ok' if ok else 'FALHOU'}")
        return ok


def ci_remote() -> tuple[bool, str]:
    r = subprocess.run(["git", "remote", "-v"], cwd=REPO, capture_output=True, text=True, check=False)  # noqa: S607
    if not r.stdout.strip():
        return False, "repositório sem remoto no GitHub: o CI (.github/workflows/ci.yml) nunca rodou"
    gh = shutil.which("gh")
    if not gh:
        return False, "remoto existe, mas o gh não está instalado para conferir a última execução do CI"
    run = subprocess.run(  # noqa: S603
        [gh, "run", "list", "--limit", "1", "--json", "conclusion,headSha"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        last = json.loads(run.stdout)[0]
    except (ValueError, IndexError):
        return False, f"não foi possível ler o CI: {run.stderr.strip()[:200]}"
    return last.get(
        "conclusion"
    ) == "success", f"última execução do CI: {last.get('conclusion')} ({last.get('headSha', '')[:8]})"


def criteria(ev: Evidence) -> list[tuple[int, str, Callable[[Judge], bool]]]:
    def all_(*checks: bool) -> bool:
        return all(checks)

    real_doc = REPO / "docs" / "validacao-real.md"
    return [
        (1, "dev.ps1 sobe tudo nativo; portal em localhost:5173; o admin loga", lambda j: j.step("dev")),
        (
            2,
            "agente cadastrado aparece online em < 10 s e descobre as simuladas",
            lambda j: all_(
                j.e2e("coletor real: cadastro pelo portal"),
                j.go("TestDiscoveryFindsSimulatedPrintersOnDifferentPorts"),
            ),
        ),
        (
            3,
            "tela de parque com as colunas do Datacount e dados do simulador",
            lambda j: all_(
                j.e2e("coletor real: cadastro pelo portal"),
                j.e2e("login → dashboard → parque"),
                j.py("test_park_rows_filters_sort_and_levels"),
            ),
        ),
        (
            4,
            "processo do agente morto volta sozinho em < 30 s (watchdog)",
            lambda j: all_(
                j.chaos("watchdog reinicia o coletor morto em menos de 30 s"),
                j.go("TestStoppedAgentIsStartedAndTheReasonReported"),
            ),
        ),
        (
            5,
            "agente travado é recuperado pelo botão Reativar (watchdog)",
            lambda j: all_(
                j.py("test_reactivate_three_outcomes"),
                j.go("TestHealthFailingThreeTimesRestarts"),
                j.e2e("coletor real: cadastro pelo portal"),
            ),
        ),
        (
            6,
            "MASTER desligado: STANDBY assume em ≤ 3,5 min, sem duplicar",
            lambda j: all_(
                j.chaos("STANDBY assume em até 3,5 min quando o MASTER cai"),
                j.chaos("novo MASTER varre e lê as 8 impressoras"),
                j.chaos("sem leituras duplicadas entre coletores (anti-duplicidade)"),
                j.py("test_cluster_anti_duplication_discards_and_records"),
            ),
        ),
        (
            7,
            "internet cortada por 1 h: nenhuma leitura perdida",
            lambda j: all_(
                j.fact(
                    f"queda simulada de {ev.chaos_outage:g} min (exigido: 60)",
                    ev.chaos_outage >= OUTAGE_MINUTES_REQUIRED,
                ),
                j.chaos("leituras ficam na fila local durante a queda"),
                j.chaos("fila local esvazia sozinha quando o servidor volta"),
                j.chaos("leituras feitas durante a queda chegaram ao banco (nenhuma perdida)"),
                j.chaos("nenhum item em dead-letter nos coletores"),
            ),
        ),
        (
            8,
            "coletor offline gera e-mail e notificação no portal em ≤ 10 min",
            lambda j: all_(
                j.e2e("coletor real: cadastro pelo portal"),
                j.py("test_collector_offline_groups_devices_and_resolves"),
            ),
        ),
        (
            9,
            "atualização com binário quebrado faz rollback automático",
            lambda j: all_(
                j.go("TestBrokenVersionIsRolledBackAndReportedFailed"),
                j.go("TestUnhealthyVersionIsRolledBackAutomatically"),
            ),
        ),
        (
            10,
            "contador que regride vira alerta e não entra na produção",
            lambda j: all_(
                j.py("test_regression_is_stored_flagged_and_alerted"),
                j.py("test_production_skips_regressions_and_uses_adjustments"),
                j.go("TestCounterRegressionScenario"),
            ),
        ),
        (
            11,
            "leitura de corte e API do ERP com os valores corretos",
            lambda j: all_(j.py("test_cutoff_and_billing"), j.py("test_readings_cutoff_and_devices")),
        ),
        (
            12,
            "Canon e Konica simuladas batem com os snmprec (Konica total = PB + cor)",
            lambda j: all_(j.go("TestProfilesOverRealSNMP"), j.py_module("test_snmpsim_recordings")),
        ),
        (
            13,
            "economia de energia que responde na 2ª tentativa não fica desconectada",
            lambda j: j.go("TestEnergySavingPrinterAnswersOnSecondAttempt"),
        ),
        (
            14,
            "túnel abre a página web; IP não cadastrado é recusado e auditado",
            lambda j: all_(j.py("test_open_rules_permissions_and_denied_ip"), j.py("test_tunnel_end_to_end")),
        ),
        (
            15,
            "Windows: 2 serviços, recuperação, atualização, rollback e desinstalação",
            lambda j: j.step("installer_full"),
        ),
        (16, "instalador recusa Windows 7/8 com mensagem clara", lambda j: j.step("installer_checks")),
        (
            17,
            "soak de 30 min sem crescer memória; carga de 20.000 com parque < 1 s",
            lambda j: all_(
                j.step("soak"),
                j.fact(
                    f"soak de {ev.soak.get('minutes', 0):g} min (exigido: 30)",
                    float(ev.soak.get("minutes", 0)) >= SOAK_MINUTES_REQUIRED,
                ),
                j.step("load"),
                j.fact(
                    f"carga de {ev.load.get('devices', 0)} equipamentos (exigido: 20.000)",
                    int(ev.load.get("devices", 0)) >= LOAD_DEVICES_REQUIRED,
                ),
            ),
        ),
        (
            18,
            "todos os testes passam (e no CI) e a aceitação mostra tudo OK",
            lambda j: all_(
                *[
                    j.step(s)
                    for s in ("lint", "go", "pytest", "vitest", "e2e", "chaos", "soak", "load", "installer")
                ],
                j.fact(ci_remote()[1], ci_remote()[0]),
            ),
        ),
        (
            19,
            "rede real: impressoras descobertas e contadores aprovados pelo usuário",
            lambda j: all_(
                j.fact(
                    "docs/validacao-real.md com a aprovação do usuário",
                    real_doc.exists() and "Aprovado pelo usuário" in real_doc.read_text(encoding="utf-8"),
                ),
                j.go("TestRealRecordings"),
            ),
        ),
        (
            20,
            "Descobertas: pendente até ativar (ou ativação automática); descartado não é lido",
            lambda j: all_(
                j.py("test_new_device_waits_in_discoveries_until_activated"),
                j.py("test_discarded_device_goes_to_ignored_serials_and_items_are_dropped"),
                j.py("test_site_auto_activation_and_decision_permissions"),
            ),
        ),
        (
            21,
            "troca de toner com rendimento; 5 atolamentos em 3 dias = atolamento recorrente",
            lambda j: all_(
                j.py("test_level_going_up_records_replacement_with_yield"),
                j.py("test_printer_errors_recurrent_jam_and_disabled_rule"),
            ),
        ),
        (
            22,
            "listas paginadas no servidor; nenhuma resposta com senha ou hash",
            lambda j: all_(
                j.py("test_every_list_is_paginated_or_bounded"),
                j.py("test_no_response_schema_carries_password_or_hash"),
                j.py("test_real_responses_never_contain_password_or_hash"),
            ),
        ),
    ]


def judge(ev: Evidence, work: Path) -> bool:
    rows = []
    md = ["# Aceitação — seção 15 do PROMPT", "", f"Execução: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", ""]
    md += ["| Etapa | Resultado | Tempo | Detalhe |", "|---|---|---|---|"]
    for name, st in ev.steps.items():
        md.append(f"| {name} | {'OK' if st.ok else 'FALHOU'} | {st.seconds / 60:.1f} min | {st.detail} |")
    md += ["", "| # | Critério | Resultado |", "|---|---|---|"]
    all_ok = True
    details: list[str] = []
    for num, title, check in criteria(ev):
        j = Judge(ev)
        ok = check(j)
        all_ok &= ok
        rows.append({"item": num, "criterio": title, "ok": ok, "provas": j.lines})
        md.append(f"| {num} | {title} | {'OK' if ok else '**FALHOU**'} |")
        details += [
            f"### {num}. {title} — {'OK' if ok else 'FALHOU'}",
            *[f"- {line}" for line in j.lines],
            "",
        ]
        log(f"{'OK     ' if ok else 'FALHOU '} {num:>2}. {title}")
        if not ok:
            for line in j.lines:
                if "FALHOU" in line or "ausente" in line or "fail" in line or "não" in line:
                    log(f"           ↳ {line}")
    md += ["", "## Provas por item", "", *details]
    (work / "relatorio.md").write_text("\n".join(md), encoding="utf-8")
    (work / "relatorio.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_count = sum(1 for r in rows if r["ok"])
    log(f"RESULTADO: {ok_count}/{len(rows)} critérios OK (relatório em {work / 'relatorio.md'})")
    return all_ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip", default="", help=f"etapas a pular, separadas por vírgula: {', '.join(STEPS)}")
    ap.add_argument("--soak-minutes", type=float, default=30)
    ap.add_argument("--outage-minutes", type=float, default=60, help="queda de internet do teste de caos")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if os.environ.get("APP_ENV") == "production":
        sys.stderr.write("ERRO: aceitação só para desenvolvimento/CI\n")
        return 1
    unknown = {s.strip() for s in args.skip.split(",") if s.strip()} - set(STEPS)
    if unknown:
        sys.stderr.write(f"ERRO: etapas desconhecidas em --skip: {sorted(unknown)}\n")
        return 2
    runner = Runner(args)
    try:
        runner.execute()
    finally:
        runner.dev_stop()
    return 0 if judge(runner.ev, runner.work) else 1


if __name__ == "__main__":
    raise SystemExit(main())
