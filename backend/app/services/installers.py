"""Downloads (PROMPT 10.11 / Fase 8): instaladores publicados pelo superadmin (setup.exe do Windows, .deb e
.tar.gz por arquitetura) e o link público do instalador, válido enquanto o código de cadastro do coletor
for válido — o técnico baixa no PC do cliente sem login no portal."""

import asyncio
import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.principal import Principal
from app.core.product import REPO_ROOT, get_product
from app.models import AgentEnrollmentCode, Installer, User
from app.schemas.installers import InstallerOut
from app.services import audit
from app.services.releases import version_key

MAX_INSTALLER_BYTES = 200 * 1024 * 1024
LINUX_ARCHES = ("amd64", "386", "arm64", "arm")
SUFFIX = {"windows": ".exe", "deb": ".deb", "tar": ".tar.gz"}
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~+-]{2,200}$")
INSTALL_SH = REPO_ROOT / "installer" / "linux" / "install.sh"


def _require_publisher(p: Principal) -> None:
    if not p.is_superadmin:
        raise forbidden("Só o superadmin publica instaladores")


def _out(i: Installer, latest: set[uuid.UUID], by: str | None = None) -> InstallerOut:
    return InstallerOut(
        id=i.id,
        kind=i.kind,
        arch=i.arch,
        version=i.version,
        filename=i.filename,
        size_bytes=i.size_bytes,
        sha256=i.sha256,
        notes=i.notes,
        withdrawn=i.withdrawn,
        latest=i.id in latest,
        created_at=i.created_at,
        published_by=by,
    )


def _latest_ids(rows: list[Installer]) -> set[uuid.UUID]:
    best: dict[tuple[str, str], Installer] = {}
    for i in rows:
        if i.withdrawn:
            continue
        cur = best.get((i.kind, i.arch))
        if cur is None or (version_key(i.version) or ()) > (version_key(cur.version) or ()):
            best[(i.kind, i.arch)] = i
    return {i.id for i in best.values()}


async def list_installers(session: AsyncSession, p: Principal) -> list[InstallerOut]:
    p.require("agents.read")
    rows = list(
        (
            await session.execute(
                select(Installer, User.name)
                .outerjoin(User, User.id == Installer.published_by)
                .order_by(Installer.created_at.desc())
            )
        ).tuples()
    )
    latest = _latest_ids([i for i, _ in rows])
    return [_out(i, latest, by) for i, by in rows]


def _validate(kind: str, arch: str, version: str, filename: str) -> None:
    if kind not in SUFFIX:
        raise bad_request("invalid_kind", "Tipo deve ser windows, deb ou tar")
    if kind == "windows" and arch != "all":
        raise bad_request("invalid_arch", "O setup.exe do Windows cobre todas as arquiteturas (arch=all)")
    if kind != "windows" and arch not in LINUX_ARCHES:
        raise bad_request("invalid_arch", "Arquitetura Linux: amd64, 386, arm64 ou arm")
    if version_key(version) is None:
        raise bad_request("invalid_version", "Versão no formato semver (ex.: 1.2.0)")
    if not FILENAME_RE.match(filename) or not filename.endswith(SUFFIX[kind]):
        raise bad_request("invalid_filename", f"Nome de arquivo inválido (deve terminar em {SUFFIX[kind]})")


async def publish(
    session: AsyncSession,
    settings: Settings,
    p: Principal,
    *,
    kind: str,
    arch: str,
    version: str,
    filename: str,
    notes: str | None,
    data: bytes,
) -> InstallerOut:
    _require_publisher(p)
    _validate(kind, arch, version, filename)
    if not data:
        raise bad_request("empty_file", "Arquivo vazio")
    sha = hashlib.sha256(data).hexdigest()
    path = Path(settings.storage_dir) / "installers" / f"{kind}-{arch}-{version}" / filename

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    row = Installer(
        kind=kind,
        arch=arch,
        version=version,
        filename=filename,
        file_path=str(path),
        size_bytes=len(data),
        sha256=sha,
        notes=(notes or "").strip() or None,
        published_by=p.user_id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise conflict("installer_exists", f"Já existe {kind}/{arch} na versão {version}") from exc
    await asyncio.to_thread(_write)
    await audit.record(
        session,
        p,
        action="installer.publish",
        entity="installer",
        entity_id=row.id,
        reseller_id=None,
        after={"kind": kind, "arch": arch, "version": version, "sha256": sha, "size": len(data)},
    )
    return _out(row, {row.id})


async def set_withdrawn(
    session: AsyncSession, p: Principal, installer_id: uuid.UUID, withdrawn: bool
) -> None:
    _require_publisher(p)
    row = await session.get(Installer, installer_id)
    if row is None:
        raise not_found("Instalador")
    row.withdrawn = withdrawn
    await audit.record(
        session,
        p,
        action="installer.withdraw" if withdrawn else "installer.restore",
        entity="installer",
        entity_id=row.id,
        reseller_id=None,
        after={"kind": row.kind, "arch": row.arch, "version": row.version},
    )


async def get_for_download(session: AsyncSession, p: Principal, installer_id: uuid.UUID) -> Installer:
    p.require("agents.read")
    row = await session.get(Installer, installer_id)
    if row is None or not await asyncio.to_thread(Path(row.file_path).is_file):
        raise not_found("Instalador")
    return row


# ----------------------------------------------------------------------------- link público


async def _valid_code(session: AsyncSession, code: str) -> AgentEnrollmentCode:
    row = (
        await session.execute(select(AgentEnrollmentCode).where(AgentEnrollmentCode.code == code.upper()))
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= datetime.now(UTC):
        # Mesma resposta para código inexistente, usado ou expirado.
        raise not_found("Código de cadastro válido")
    return row


async def public_installer(
    session: AsyncSession, *, code: str, platform: str, arch: str | None, fmt: str | None
) -> Installer:
    await _valid_code(session, code)
    if platform == "windows":
        kind, want_arch = "windows", "all"
    else:
        kind = fmt or "deb"
        if kind not in ("deb", "tar") or arch not in LINUX_ARCHES:
            raise bad_request(
                "invalid_target", "Linux: informe arch (amd64, 386, arm64, arm) e format (deb ou tar)"
            )
        want_arch = arch
    rows = list(
        (
            await session.execute(
                select(Installer).where(
                    Installer.kind == kind, Installer.arch == want_arch, Installer.withdrawn.is_(False)
                )
            )
        ).scalars()
    )
    rows = [r for r in rows if await asyncio.to_thread(Path(r.file_path).is_file)]
    if not rows:
        raise not_found("Instalador publicado para este sistema")
    return max(rows, key=lambda r: version_key(r.version) or ())


async def install_script(session: AsyncSession, settings: Settings, code: str) -> str:
    """install.sh com o servidor e o código preenchidos (curl ... | sudo sh)."""
    await _valid_code(session, code)
    product = get_product()
    text = await asyncio.to_thread(INSTALL_SH.read_text, encoding="utf-8")
    values = {
        "PRODUCT": product.name,
        "DATA_DIR": f"/var/lib/{product.slug}",
        "AGENT_SERVICE": f"{product.service_prefix}Agent",
        "WATCHDOG_SERVICE": f"{product.service_prefix}Watchdog",
        "SERVER": settings.public_server_url.rstrip("/"),
        "CODE": code.upper(),
    }
    for k, v in values.items():
        text = text.replace(f"@{k}@", v)
    return text
