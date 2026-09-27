"""Sobe as 8 impressoras simuladas (snmpsim) para o E2E do portal, em portas próprias.

Usa portas separadas das do dev.ps1 (1161-1168) para não disputar com o ambiente de desenvolvimento:
impressora NN escuta em 127.0.0.1:(base + NN). Quando todas respondem ao sysObjectID, abre a porta
TCP --ready-port: o Playwright (webServer) espera por ela. Encerrar este processo encerra os simuladores.

Uso: .venv\\Scripts\\python scripts\\e2e_sims.py [--base 12160] [--ready-port 12160]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SIM_ROOT = REPO / "profiles" / "recordings" / "sim"
CACHE = REPO / "var" / "e2e" / "snmpsim-cache"
READY_TIMEOUT_S = 90
# GET v2c (comunidade "public", request-id 1) de sysObjectID.0, montado à mão em BER: a sonda não
# depende do motor do pysnmp, que deixava de receber as respostas quando várias portas começavam mudas.
SNMP_GET_SYS_OBJECT_ID = bytes.fromhex(
    "302602010104067075626c6963a019020101020100020100300e300c06082b060102010102000500"
)
GET_RESPONSE_PDU = 0xA2


def responder() -> Path:
    scripts = Path(sys.executable).parent
    exe = scripts / (
        "snmpsim-command-responder.exe" if sys.platform == "win32" else "snmpsim-command-responder"
    )
    if not exe.exists():
        raise SystemExit(f"snmpsim não encontrado em {exe} (pip install -r backend/requirements-dev.lock)")
    return exe


def sim_dirs() -> list[tuple[int, Path]]:
    dirs = []
    for d in sorted(SIM_ROOT.iterdir()):
        if d.is_dir() and d.name[:2].isdigit() and (d / "public.snmprec").exists():
            dirs.append((int(d.name[:2]), d))
    if not dirs:
        raise SystemExit(f"Nenhuma impressora simulada em {SIM_ROOT}")
    return dirs


def answers(port: int) -> bool:
    """True quando o simulador devolve um GetResponse ao GET de sysObjectID."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(1)
        try:
            sock.sendto(SNMP_GET_SYS_OBJECT_ID, ("127.0.0.1", port))
            data, _ = sock.recvfrom(4096)
        except OSError:  # timeout ou porta ainda fechada (ICMP port unreachable no Windows)
            return False
    return GET_RESPONSE_PDU in data


def wait_ready(ports: list[int], procs: list[subprocess.Popen[bytes]]) -> None:
    pending = set(ports)
    deadline = time.monotonic() + READY_TIMEOUT_S
    while pending:
        for p in procs:
            if p.poll() is not None:
                raise SystemExit(f"snmpsim encerrou com código {p.returncode}: {p.args!r}")
        pending -= {port for port in pending if answers(port)}
        if pending and time.monotonic() > deadline:
            raise SystemExit(
                f"Simuladores sem resposta em {READY_TIMEOUT_S} s nas portas {sorted(pending)}; "
                f"veja {CACHE.parent / 'snmpsim-*.log'}"
            )
        time.sleep(0.3)


async def serve_ready(port: int, procs: list[subprocess.Popen[bytes]]) -> None:
    async def ok(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(ok, "127.0.0.1", port)
    async with server:
        # Popen não tem espera assíncrona: confere a cada segundo se algum simulador caiu.
        while all(p.poll() is None for p in procs):  # noqa: ASYNC110
            await asyncio.sleep(1)
    dead = [p.args for p in procs if p.poll() is not None]  # para a mensagem de erro
    raise SystemExit(f"Simulador encerrou inesperadamente: {dead!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=int, default=12160)
    parser.add_argument("--ready-port", type=int, default=12160)
    args = parser.parse_args(argv)
    exe = responder()
    procs: list[subprocess.Popen[bytes]] = []
    ports: list[int] = []
    logs = []
    try:
        for n, d in sim_dirs():
            port = args.base + n
            ports.append(port)
            cache = CACHE / d.name
            cache.mkdir(parents=True, exist_ok=True)  # o snmpsim não cria a pasta e fica sem responder
            log = (CACHE.parent / f"snmpsim-{d.name}.log").open("wb")
            logs.append(log)
            procs.append(
                subprocess.Popen(  # noqa: S603 - executável e argumentos montados aqui, sem entrada externa
                    [
                        str(exe),
                        f"--data-dir={d}",
                        f"--cache-dir={cache}",
                        f"--agent-udpv4-endpoint=127.0.0.1:{port}",
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        wait_ready(ports, procs)
        print(f"{len(ports)} impressoras simuladas prontas nas portas {ports}", flush=True)  # noqa: T201
        asyncio.run(serve_ready(args.ready_port, procs))
    finally:
        for p in procs:
            with contextlib.suppress(OSError):
                p.terminate()
        for p in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=5)
        for log in logs:
            log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
