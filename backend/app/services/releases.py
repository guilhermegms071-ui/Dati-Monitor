"""Signed releases of dm-agent / dm-watchdog (PROMPT 5.2).

The server only stores and serves binaries that carry a valid ed25519 signature made OUTSIDE the server
(`dm-tool sign`, locally or in CI): it checks the signature with the public key — the same one embedded
in the watchdog — so a tampered or mislabelled upload is refused before any agent sees it. The signed
message binds component, version, OS, architecture and sha256 (`proto.release_message`), so a signed
old binary cannot be replayed under a new version number.
"""

import asyncio
import base64
import binascii
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.principal import Principal
from app.models import Agent, AgentRelease, Command
from app.schemas import agent as proto
from app.services import audit

COMPONENTS = ("agent", "watchdog")
OS_ARCH = {
    "windows": ("amd64", "386", "arm64"),
    "linux": ("amd64", "386", "arm64", "arm"),
}
CHANNELS = ("canary", "stable")
MAX_RELEASE_BYTES = 80 * 1024 * 1024
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def version_key(version: str) -> tuple[Any, ...] | None:
    """Semver ordering key (pre-releases before the final version); None if not semver."""
    m = _VERSION.match(version.strip())
    if not m:
        return None
    major, minor, patch, pre = int(m[1]), int(m[2]), int(m[3]), m[4]
    if pre is None:
        return (major, minor, patch, 1, ())
    parts = tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre.split("."))
    return (major, minor, patch, 0, parts)


def is_newer(candidate: str, current: str | None) -> bool:
    new = version_key(candidate)
    if new is None:
        return False
    old = version_key(current or "")
    return old is None or new > old


def verify_signature(public_key: bytes, message: bytes, signature_b64: str) -> bool:
    try:
        sig = base64.b64decode(signature_b64.strip(), validate=True)
    except (binascii.Error, ValueError):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(sig, message)
    except (InvalidSignature, ValueError):
        return False
    return True


def _store(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def binary_name(component: str, os_: str) -> str:
    return f"dm-{component}{'.exe' if os_ == 'windows' else ''}"


def download_path(release_id: uuid.UUID) -> str:
    return f"/api/agent/releases/{release_id}/file"


def update_params(release: AgentRelease) -> dict[str, Any]:
    """What the executor (watchdog or agent) needs to download, verify and install the release."""
    return proto.UpdateParams(
        release_id=str(release.id),
        component=release.component,
        version=release.version,
        os=release.os,
        arch=release.arch,
        sha256=release.sha256,
        signature=release.signature,
        size_bytes=release.size_bytes,
        url=download_path(release.id),
    ).model_dump()


def _require_publisher(p: Principal) -> None:
    # Publicar binário que roda em todos os clientes é da plataforma, não de uma revenda.
    if not p.is_superadmin:
        raise forbidden("Só o superadmin publica versões do coletor")


@dataclass(frozen=True)
class PublishInput:
    component: str
    version: str
    os: str
    arch: str
    channel: str
    rollout_percent: int
    notes: str | None
    signature: str


async def publish(
    session: AsyncSession, settings: Settings, p: Principal, data: PublishInput, binary: bytes
) -> AgentRelease:
    _require_publisher(p)
    if data.component not in COMPONENTS:
        raise bad_request("invalid_component", "Componente deve ser agent ou watchdog")
    if data.arch not in OS_ARCH.get(data.os, ()):
        raise bad_request("invalid_target", f"Alvo {data.os}/{data.arch} não é um dos alvos de build")
    if data.channel not in CHANNELS:
        raise bad_request("invalid_channel", "Canal deve ser canary ou stable")
    if not 0 <= data.rollout_percent <= 100:  # noqa: PLR2004
        raise bad_request("invalid_rollout", "Liberação gradual entre 0 e 100%")
    if version_key(data.version) is None:
        raise bad_request("invalid_version", "Versão no formato 1.2.3 (ou 1.2.3-beta.1)")
    if not binary:
        raise bad_request("empty_file", "Arquivo vazio")
    if len(binary) > MAX_RELEASE_BYTES:
        raise bad_request("file_too_large", "Arquivo acima de 80 MB")
    public_key = settings.release_public_key_bytes
    if public_key is None:
        raise conflict("no_release_key", "Servidor sem chave pública de releases (RELEASE_PUBLIC_KEY)")
    digest = hashlib.sha256(binary).hexdigest()
    message = proto.release_message(data.component, data.version, data.os, data.arch, digest)
    if not verify_signature(public_key, message, data.signature):
        raise bad_request(
            "invalid_signature",
            "Assinatura não confere: assine este arquivo com `dm-tool sign` usando a chave privada oficial",
        )
    exists = await session.scalar(
        select(AgentRelease.id).where(
            AgentRelease.component == data.component,
            AgentRelease.version == data.version,
            AgentRelease.os == data.os,
            AgentRelease.arch == data.arch,
        )
    )
    if exists:
        raise conflict(
            "release_exists", f"A versão {data.version} para {data.os}/{data.arch} já foi publicada"
        )
    folder = settings.storage_dir / "releases" / data.component / data.version / f"{data.os}-{data.arch}"
    path = folder / binary_name(data.component, data.os)
    await asyncio.to_thread(_store, path, binary)
    release = AgentRelease(
        component=data.component,
        version=data.version.strip(),
        os=data.os,
        arch=data.arch,
        file_path=str(path),
        size_bytes=len(binary),
        sha256=digest,
        signature=data.signature.strip(),
        channel=data.channel,
        notes=data.notes,
        rollout_percent=data.rollout_percent,
        published_at=datetime.now(UTC),
        published_by=p.user_id,
    )
    session.add(release)
    await session.flush()
    await audit.record(
        session,
        p,
        action="release.publish",
        entity="agent_release",
        entity_id=release.id,
        reseller_id=None,
        after={
            "component": release.component,
            "version": release.version,
            "target": f"{release.os}/{release.arch}",
            "sha256": digest,
            "channel": release.channel,
            "rollout_percent": release.rollout_percent,
        },
    )
    return release


async def update_release(
    session: AsyncSession, p: Principal, release_id: uuid.UUID, changes: dict[str, Any]
) -> AgentRelease:
    _require_publisher(p)
    release = await session.get(AgentRelease, release_id)
    if release is None:
        raise not_found("Versão")
    before = {k: getattr(release, k) for k in changes}
    if "channel" in changes and changes["channel"] not in CHANNELS:
        raise bad_request("invalid_channel", "Canal deve ser canary ou stable")
    if "rollout_percent" in changes and not 0 <= int(changes["rollout_percent"]) <= 100:  # noqa: PLR2004
        raise bad_request("invalid_rollout", "Liberação gradual entre 0 e 100%")
    for key, value in changes.items():
        setattr(release, key, value)
    await audit.record(
        session,
        p,
        action="release.update",
        entity="agent_release",
        entity_id=release.id,
        reseller_id=None,
        before=before,
        after=changes,
    )
    return release


async def list_releases(session: AsyncSession, p: Principal) -> list[AgentRelease]:
    # Todo usuário que pode mandar comandos precisa ver as versões para escolher em "Atualizar".
    p.require("agents.read")
    return list(
        (
            await session.execute(
                select(AgentRelease).order_by(
                    AgentRelease.published_at.desc(), AgentRelease.os, AgentRelease.arch
                )
            )
        ).scalars()
    )


@dataclass
class ReleaseStats:
    succeeded: int = 0
    failed: int = 0
    in_progress: int = 0
    canary_succeeded: int = 0
    canary_failed: int = 0

    @property
    def canary_failure_percent(self) -> float:
        done = self.canary_succeeded + self.canary_failed
        return 100.0 * self.canary_failed / done if done else 0.0


async def release_stats(session: AsyncSession, release_ids: list[uuid.UUID]) -> dict[uuid.UUID, ReleaseStats]:
    """Update outcomes per release (from the `update` commands), split by the agents' channel."""
    out = {rid: ReleaseStats() for rid in release_ids}
    if not release_ids:
        return out
    rel = Command.params["release_id"].astext
    rows = await session.execute(
        select(rel, Agent.update_channel, Command.state, func.count())
        .join(Agent, Agent.id == Command.agent_id)
        .where(Command.type == "update", rel.in_([str(r) for r in release_ids]))
        .group_by(rel, Agent.update_channel, Command.state)
    )
    for rid, channel, state, n in rows.tuples():
        s = out[uuid.UUID(rid)]
        if state == "succeeded":
            s.succeeded += n
            if channel == "canary":
                s.canary_succeeded += n
        elif state in ("failed", "expired"):
            s.failed += n
            if channel == "canary":
                s.canary_failed += n
        elif state != "cancelled":
            s.in_progress += n
    return out


def agent_target(agent: Agent) -> tuple[str, str] | None:
    """(os, arch) of the agent's PC as used by releases, or None if it never reported them."""
    if not agent.arch:
        return None
    return agent.kind, agent.arch


def channels_for(agent: Agent) -> tuple[str, ...]:
    return ("canary", "stable") if agent.update_channel == "canary" else ("stable",)


async def find_release(session: AsyncSession, agent: Agent, component: str, version: str) -> AgentRelease:
    """The published release of `version` matching the agent's PC (manual update from the portal)."""
    target = agent_target(agent)
    if target is None:
        raise conflict("agent_target_unknown", "O coletor ainda não informou o sistema/arquitetura do PC")
    release = (
        await session.execute(
            select(AgentRelease).where(
                AgentRelease.component == component,
                AgentRelease.version == version.strip(),
                AgentRelease.os == target[0],
                AgentRelease.arch == target[1],
            )
        )
    ).scalar_one_or_none()
    if release is None:
        raise bad_request(
            "release_not_found", f"Versão {version} não publicada para {target[0]}/{target[1]} ({component})"
        )
    if release.yanked:
        raise conflict("release_yanked", f"A versão {version} foi retirada e não pode ser instalada")
    return release


async def get_release_file(session: AsyncSession, agent: Agent, release_id: uuid.UUID) -> Path:
    """Binary download for the agent/watchdog executing an `update` of this release."""
    release = await session.get(AgentRelease, release_id)
    if release is None or release.yanked:
        raise not_found("Versão")
    target = agent_target(agent)
    if target is not None and (release.os, release.arch) != target:
        raise not_found("Versão")
    path = Path(release.file_path)
    if not await asyncio.to_thread(path.is_file):
        raise not_found("Arquivo da versão")
    return path
