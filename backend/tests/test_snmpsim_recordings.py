"""Integration: every simulated printer in profiles/recordings/sim is served by the real snmpsim
and answers GET and WALK with the standard Printer-MIB OIDs (PROMPT sections 6.2 and 13)."""

import asyncio
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    get_cmd,
    walk_cmd,
)
from pysnmp.proto.rfc1902 import ObjectIdentifier

from app.core.product import REPO_ROOT

SIM_DIR = REPO_ROOT / "profiles" / "recordings" / "sim"
RESPONDER = Path(sys.executable).with_name(
    "snmpsim-command-responder.exe" if sys.platform == "win32" else "snmpsim-command-responder"
)
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
HR_DEVICE_TYPE = "1.3.6.1.2.1.25.3.2.1.2.1"
SERIAL = "1.3.6.1.2.1.43.5.1.1.17.1"


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _recordings() -> list[Path]:
    return sorted(p for p in SIM_DIR.iterdir() if p.is_dir() and (p / "public.snmprec").exists())


def _oid_key(oid: str) -> tuple[int, ...]:
    return tuple(int(x) for x in oid.split("."))


@pytest.mark.parametrize("recording", _recordings(), ids=lambda p: p.name)
def test_recording_is_sorted_and_well_formed(recording: Path) -> None:
    oids = []
    for n, line in enumerate((recording / "public.snmprec").read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split("|", 2)
        assert len(parts) == 3, f"linha {n} inválida: {line!r}"
        oids.append(parts[0])
    keys = [_oid_key(o) for o in oids]
    assert keys == sorted(keys), "snmprec precisa estar ordenado numericamente por OID"
    assert len(set(keys)) == len(keys), "OID duplicado"


async def _get(port: int, *oids: str) -> dict[str, str]:
    engine = SnmpEngine()
    try:
        target = await UdpTransportTarget.create(("127.0.0.1", port), timeout=2, retries=2)
        err_ind, err_status, _, binds = await get_cmd(
            engine,
            CommunityData("public", mpModel=1),  # noqa: S508 - o simulador só fala v2c
            target,
            ContextData(),
            *[ObjectType(ObjectIdentity(o)) for o in oids],
            lookupMib=False,
        )
    finally:
        engine.close_dispatcher()
    assert err_ind is None, err_ind
    assert not err_status, err_status.prettyPrint()
    # lookupMib=False devolve valores crus; OIDs em forma numérica.
    return {
        str(name): str(value) if isinstance(value, ObjectIdentifier) else value.prettyPrint()
        for name, value in binds
    }


async def _walk(port: int, root: str) -> list[str]:
    engine = SnmpEngine()
    found: list[str] = []
    try:
        target = await UdpTransportTarget.create(("127.0.0.1", port), timeout=2, retries=2)
        async for err_ind, err_status, _, binds in walk_cmd(
            engine,
            CommunityData("public", mpModel=1),  # noqa: S508 - o simulador só fala v2c
            target,
            ContextData(),
            ObjectType(ObjectIdentity(root)),
            lexicographicMode=False,
            lookupMib=False,
        ):
            assert err_ind is None, err_ind
            assert not err_status, err_status.prettyPrint()
            found.extend(str(name) for name, _ in binds)
    finally:
        engine.close_dispatcher()
    return found


@pytest.fixture(scope="module", params=_recordings(), ids=lambda p: p.name)
def simulator(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Path, int]]:
    recording: Path = request.param
    port = _free_udp_port()
    workdir = tmp_path_factory.mktemp("snmpsim")
    log_path = workdir / "snmpsim.log"
    # A saída vai para arquivo: um PIPE não lido enche (≈4 KB no Windows) e trava o snmpsim.
    with log_path.open("wb") as log:
        proc = subprocess.Popen(  # noqa: S603 - fixed executable from the venv
            [
                str(RESPONDER),
                f"--data-dir={recording}",
                f"--cache-dir={workdir / 'cache'}",
                f"--agent-udpv4-endpoint=127.0.0.1:{port}",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                out = log_path.read_text(encoding="utf-8", errors="replace")
                pytest.fail(f"snmpsim terminou com código {proc.returncode}:\n{out}")
            try:
                asyncio.run(_get(port, SYS_OBJECT_ID))
                break
            except AssertionError:
                if time.monotonic() > deadline:
                    out = log_path.read_text(encoding="utf-8", errors="replace")
                    pytest.fail(f"snmpsim não respondeu em 30 s. Log:\n{out}")
                time.sleep(0.5)
        yield recording, port
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_simulated_printer_answers_get(simulator: tuple[Path, int]) -> None:
    recording, port = simulator
    values = asyncio.run(_get(port, SYS_OBJECT_ID, HR_DEVICE_TYPE, SERIAL))
    expected = {
        line.split("|", 2)[0]: line.split("|", 2)[2]
        for line in (recording / "public.snmprec").read_text(encoding="utf-8").splitlines()
    }
    assert values[HR_DEVICE_TYPE] == "1.3.6.1.2.1.25.3.1.5"
    assert values[SERIAL] == expected[SERIAL]
    assert values[SYS_OBJECT_ID] == expected[SYS_OBJECT_ID]


def test_simulated_printer_supplies_table_walks_in_order(simulator: tuple[Path, int]) -> None:
    _, port = simulator
    oids = asyncio.run(_walk(port, "1.3.6.1.2.1.43.11.1.1"))
    assert oids, "tabela prtMarkerSupplies vazia"
    assert [_oid_key(o) for o in oids] == sorted(_oid_key(o) for o in oids)
