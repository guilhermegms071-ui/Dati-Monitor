"""Pacotes Linux do coletor (Fase 8): o .deb gerado sem dpkg-deb segue o formato do Debian (ar + control +
data), é reprodutível e leva os scripts preenchidos; o install.sh baixa o pacote certo, cadastra, instala
e inicia os serviços (rodado de verdade com comandos falsos no PATH)."""

import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.conftest import load_script

build_linux = load_script("build_linux")
REPO = Path(__file__).resolve().parents[2]


def fake_dist(tmp: Path, arch: str) -> Path:
    d = tmp / f"linux-{arch}"
    d.mkdir(parents=True)
    for b in ("dm-agent", "dm-watchdog", "dm-tool"):
        (d / b).write_bytes(f"#!/bin/sh\necho {b} {arch}\n".encode())
    return tmp


def parse_ar(data: bytes) -> list[tuple[str, bytes]]:
    assert data.startswith(b"!<arch>\n")
    pos, out = 8, []
    while pos < len(data):
        header = data[pos : pos + 60]
        assert header[58:60] == b"`\n", "cabeçalho ar inválido"
        name = header[:16].decode().strip()
        assert header[40:48].decode().strip() == "100644"
        size = int(header[48:58].decode().strip())
        body = data[pos + 60 : pos + 60 + size]
        out.append((name, body))
        pos += 60 + size + (size % 2)
    return out


def members(tgz: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as t:
        return {m.name: m for m in t.getmembers()}


def read(tgz: bytes, name: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as t:
        f = t.extractfile(name)
        assert f is not None
        return f.read().decode("utf-8")


def test_deb_layout_control_and_scripts(tmp_path: Path) -> None:
    product = build_linux.load_product()
    dist = fake_dist(tmp_path / "dist", "arm")
    name, deb = build_linux.build_deb(product, "1.2.0-rc.1", "arm", dist / "linux-arm")
    assert name == "dati-monitor-agent_1.2.0~rc.1_armhf.deb"
    parts = parse_ar(deb)
    assert [n for n, _ in parts] == ["debian-binary", "control.tar.gz", "data.tar.gz"]
    assert parts[0][1] == b"2.0\n"
    control_tgz, data_tgz = parts[1][1], parts[2][1]

    control = read(control_tgz, "./control")
    assert "Package: dati-monitor-agent\n" in control
    assert "Version: 1.2.0~rc.1\n" in control
    assert "Architecture: armhf\n" in control
    assert "Depends: systemd\n" in control
    ctl = members(control_tgz)
    for script in ("./postinst", "./prerm", "./postrm"):
        assert stat.S_IMODE(ctl[script].mode) == 0o755
        text = read(control_tgz, script)
        assert "@" not in text.replace("$@", ""), f"marcador não preenchido em {script}"
    postinst = read(control_tgz, "./postinst")
    assert 'DATA_DIR="/var/lib/dati-monitor"' in postinst
    assert "/etc/systemd/system/DatiMonitorAgent.service" in postinst
    assert 'rm -rf "/var/lib/dati-monitor"' in read(control_tgz, "./postrm")

    data = members(data_tgz)
    for b in ("dm-agent", "dm-watchdog", "dm-tool"):
        info = data[f"./usr/bin/{b}"]
        assert stat.S_IMODE(info.mode) == 0o755
        assert (info.uname, info.gname) == ("root", "root")
    md5 = read(control_tgz, "./md5sums")
    assert "usr/bin/dm-agent" in md5

    # Reprodutível: mesma entrada, mesmos bytes.
    _, again = build_linux.build_deb(product, "1.2.0-rc.1", "arm", dist / "linux-arm")
    assert again == deb


def test_cli_builds_every_arch_and_reports_missing_binaries(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    for arch in build_linux.ARCHES:
        fake_dist(dist, arch)
    out = tmp_path / "out"
    assert build_linux.main(["--version", "1.0.0", "--dist", str(dist), "--out", str(out)]) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted(
        [f"dati-monitor-agent_1.0.0_{d}.deb" for d in build_linux.ARCHES.values()]
        + [f"dati-monitor-agent-1.0.0-linux-{a}.tar.gz" for a in build_linux.ARCHES]
    )
    with tarfile.open(out / "dati-monitor-agent-1.0.0-linux-386.tar.gz") as t:
        assert sorted(t.getnames()) == ["dm-agent", "dm-tool", "dm-watchdog"]
    assert build_linux.main(["--version", "1.0.0", "--dist", str(tmp_path / "vazio"), "--out", str(out)]) == 1
    assert build_linux.main(["--version", "1.0", "--dist", str(dist), "--out", str(out)]) == 2


FAKE = """#!/bin/sh
echo "$(basename "$0") $*" >> "$CALLS"
case "$(basename "$0")" in
  curl) while [ $# -gt 0 ]; do [ "$1" = "-o" ] && echo pacote > "$2"; shift; done ;;
  uname) echo x86_64 ;;
  id) echo 0 ;;
esac
exit 0
"""


@pytest.fixture
def sh() -> str:
    found = shutil.which("sh") or shutil.which("bash")
    if found is None:
        pytest.fail("o teste do install.sh precisa de um shell POSIX (sh/bash) no PATH")
    return found


def test_install_sh_downloads_enrolls_installs_and_starts(tmp_path: Path, sh: str) -> None:
    product = build_linux.load_product()
    script = product.fill(
        (REPO / "installer" / "linux" / "install.sh").read_text(encoding="utf-8"),
        SERVER="https://monitor.exemplo.test",
        CODE="ABCD1234",
    )
    # Pasta de dados e units em caminhos de teste (o resto do script é o de produção).
    script = script.replace("/var/lib/dati-monitor", str(tmp_path / "data").replace("\\", "/"))
    script = script.replace("/etc/systemd/system/", str(tmp_path / "units").replace("\\", "/") + "/")
    (tmp_path / "install.sh").write_text(script, encoding="utf-8", newline="\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for cmd in ("curl", "dpkg", "systemctl", "dm-agent", "dm-watchdog", "uname", "id"):
        f = bindir / cmd
        f.write_text(FAKE, encoding="utf-8", newline="\n")
        f.chmod(0o755)
    calls = tmp_path / "calls.txt"
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
    }
    proc = subprocess.run(  # noqa: S603 - script do próprio repositório
        [sh, str(tmp_path / "install.sh")],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    lines = calls.read_text(encoding="utf-8").splitlines()
    order = [
        line.split()[0] for line in lines if line.split()[0] in ("curl", "dpkg", "dm-agent", "dm-watchdog")
    ]
    assert order == ["curl", "dpkg", "dm-agent", "dm-agent", "dm-watchdog"]
    curl = next(line for line in lines if line.startswith("curl"))
    assert (
        "https://monitor.exemplo.test/api/public/installer?code=ABCD1234&platform=linux&arch=amd64&format=deb"
        in curl
    )
    assert "dm-agent enroll --server https://monitor.exemplo.test --code ABCD1234" in lines
    assert "dm-agent install" in lines
    assert "dm-watchdog install" in lines
    assert "systemctl restart DatiMonitorAgent DatiMonitorWatchdog" in lines
    assert "coletor instalado e em execução" in proc.stdout

    # Código ausente: para com mensagem clara, sem tocar em nada.
    bad = subprocess.run(  # noqa: S603
        [sh, str(tmp_path / "install.sh"), "--code", "X"], env=env, capture_output=True, text=True,
        encoding="utf-8", check=False,
    )  # fmt: skip
    assert bad.returncode == 1
    assert "código de 8 caracteres" in bad.stderr
