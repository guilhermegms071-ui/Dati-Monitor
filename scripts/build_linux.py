"""Pacotes Linux do coletor (PROMPT Fase 8): um .deb e um .tar.gz por arquitetura, a partir dos binários de
scripts\\build-agent.ps1 (dist/linux-<arch>/). Sem dpkg-deb: o .deb é um arquivo `ar` com debian-binary,
control.tar.gz e data.tar.gz, montado aqui de forma reprodutível (mesma entrada = mesmos bytes).

Uso:  .venv\\Scripts\\python scripts\\build_linux.py --version 1.0.0 [--dist dist] [--out dist/installers]

Os serviços NÃO vêm como units estáticas: o postinst chama `dm-agent install` / `dm-watchdog install`,
que geram a unit com o template do Go (Restart=always, RestartSec=5, WatchdogSec=60, sd_notify), a mesma
usada sem pacote. Assim a unit tem uma fonte só.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Go GOARCH → arquitetura Debian. GOARM=6 cobre Raspberry Pi antigos e novos (armhf).
ARCHES = {"amd64": "amd64", "386": "i386", "arm64": "arm64", "arm": "armhf"}
BINARIES = ("dm-agent", "dm-watchdog", "dm-tool")
MTIME = 1_700_000_000  # fixo: pacote reprodutível
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class Product:
    name: str
    slug: str
    service_prefix: str

    @property
    def package(self) -> str:
        return f"{self.slug}-agent"

    @property
    def data_dir(self) -> str:
        return f"/var/lib/{self.slug}"

    def fill(self, text: str, **extra: str) -> str:
        values = {
            "PRODUCT": self.name,
            "DATA_DIR": self.data_dir,
            "AGENT_SERVICE": f"{self.service_prefix}Agent",
            "WATCHDOG_SERVICE": f"{self.service_prefix}Watchdog",
            **extra,
        }
        for k, v in values.items():
            text = text.replace(f"@{k}@", v)
        return text


def load_product() -> Product:
    raw = json.loads((REPO / "product.json").read_text(encoding="utf-8"))
    return Product(raw["name"], raw["slug"], raw["service_prefix"])


def deb_version(version: str) -> str:
    """Semver → Debian (1.0.0-rc.1 → 1.0.0~rc.1, que ordena antes de 1.0.0)."""
    return version.replace("-", "~", 1)


def _tar(entries: list[tuple[str, bytes, int]]) -> bytes:
    """tar.gz reprodutível (dono root, horário fixo, gzip sem nome/horário)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        dirs: set[str] = set()
        for name, _data, _mode in entries:
            parts = name.split("/")[1:-1]
            for i in range(1, len(parts) + 1):
                d = "./" + "/".join(parts[:i])
                if d not in dirs:
                    dirs.add(d)
                    info = tarfile.TarInfo(d)
                    info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, MTIME
                    tar.addfile(info)
        for name, data, mode in entries:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), mode, MTIME
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=MTIME, filename="") as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def _ar(members: list[tuple[str, bytes]]) -> bytes:
    out = bytearray(b"!<arch>\n")
    for name, data in members:
        header = f"{name:<16}{MTIME:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}".encode("ascii") + b"`\n"
        out += header + data
        if len(data) % 2:
            out += b"\n"
    return bytes(out)


def build_deb(product: Product, version: str, goarch: str, bindir: Path) -> tuple[str, bytes]:
    debarch = ARCHES[goarch]
    data_entries = []
    for b in BINARIES:
        path = bindir / b
        if not path.is_file():
            raise FileNotFoundError(f"binário ausente: {path} (rode scripts\\build-agent.ps1)")
        data_entries.append((f"./usr/bin/{b}", path.read_bytes(), 0o755))
    copyright_text = f"{product.name} — coletor de leituras de impressoras. Copyright Daticopy.\n"
    data_entries.append(
        (f"./usr/share/doc/{product.package}/copyright", copyright_text.encode("utf-8"), 0o644)
    )
    installed_kb = sum(len(d) for _n, d, _m in data_entries) // 1024 + 1
    md5sums = "".join(f"{hashlib.md5(d).hexdigest()}  {n[2:]}\n" for n, d, _m in data_entries)  # noqa: S324 - formato do dpkg
    control = (
        f"Package: {product.package}\n"
        f"Version: {deb_version(version)}\n"
        f"Architecture: {debarch}\n"
        "Maintainer: Daticopy <suporte@daticopy.com.br>\n"
        f"Installed-Size: {installed_kb}\n"
        "Depends: systemd\n"
        "Section: admin\n"
        "Priority: optional\n"
        f"Description: {product.name} - coletor de leituras de impressoras\n"
        " Le contadores, suprimentos e status das impressoras da rede por SNMP e envia\n"
        f" ao servidor {product.name}. Inclui o watchdog que mantem o coletor no ar.\n"
    )
    scripts_dir = REPO / "installer" / "linux"
    control_entries = [
        ("./control", control.encode("utf-8"), 0o644),
        ("./md5sums", md5sums.encode("ascii"), 0o644),
    ]
    for script in ("postinst", "prerm", "postrm"):
        text = product.fill((scripts_dir / script).read_text(encoding="utf-8"))
        control_entries.append((f"./{script}", text.encode("utf-8"), 0o755))
    deb = _ar(
        [
            ("debian-binary", b"2.0\n"),
            ("control.tar.gz", _tar(control_entries)),
            ("data.tar.gz", _tar(data_entries)),
        ]
    )
    return f"{product.package}_{deb_version(version)}_{debarch}.deb", deb


def build_tar(product: Product, version: str, goarch: str, bindir: Path) -> tuple[str, bytes]:
    entries = [(b, (bindir / b).read_bytes(), 0o755) for b in BINARIES]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name, data, mode in entries:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), mode, MTIME
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=MTIME, filename="") as gz:
        gz.write(raw.getvalue())
    return f"{product.package}-{version}-linux-{goarch}.tar.gz", out.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--dist", type=Path, default=REPO / "dist")
    ap.add_argument("--out", type=Path, default=REPO / "dist" / "installers")
    ap.add_argument("--arch", action="append", choices=sorted(ARCHES), help="padrão: todas")
    args = ap.parse_args(argv)
    if not VERSION_RE.match(args.version):
        print(f"ERRO: versão inválida (use semver): {args.version}", file=sys.stderr)  # noqa: T201
        return 2
    product = load_product()
    args.out.mkdir(parents=True, exist_ok=True)
    for goarch in args.arch or list(ARCHES):
        bindir = args.dist / f"linux-{goarch}"
        try:
            for name, data in (
                build_deb(product, args.version, goarch, bindir),
                build_tar(product, args.version, goarch, bindir),
            ):
                (args.out / name).write_bytes(data)
                print(f"OK  {args.out / name}  sha256 {hashlib.sha256(data).hexdigest()}")  # noqa: T201
        except FileNotFoundError as exc:
            print(f"ERRO: {exc}", file=sys.stderr)  # noqa: T201
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
