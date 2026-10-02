"""Perfis de modelos (PROMPT 6.6): versions of the read profiles, validation with the shared schema,
publishing from the portal, activating an older version, and the walk explorer (search by value)."""

import asyncio
import gzip
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, not_found
from app.core.principal import Principal
from app.core.product import REPO_ROOT
from app.models import ReadProfile, User
from app.schemas.profiles import (
    FixtureIn,
    ProfileDetail,
    ProfileSummary,
    ProfileValidation,
    ProfileVersionOut,
    WalkRow,
    WalkTree,
)
from app.services import audit
from app.services.agents import bump_all_configs
from app.services.catalog import ProfileError, validate_profile
from app.services.commands import get_walk

FIXTURES_DIR = REPO_ROOT / "profiles" / "recordings" / "real"
FIXTURE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{2,80}$")
MAX_WALK_ROWS = 500


def parse_yaml(text: str) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"YAML inválido: {exc}") from exc
    return validate_profile(doc)


def validate(text: str) -> ProfileValidation:
    try:
        doc = parse_yaml(text)
    except ProfileError as exc:
        return ProfileValidation(ok=False, error=str(exc), profile=None)
    return ProfileValidation(ok=True, error=None, profile=doc)


async def _active(session: AsyncSession) -> dict[str, ReadProfile]:
    rows = (await session.execute(select(ReadProfile).where(ReadProfile.active.is_(True)))).scalars()
    out: dict[str, ReadProfile] = {}
    for r in rows:
        if r.profile_key not in out or r.version > out[r.profile_key].version:
            out[r.profile_key] = r
    return out


def _match(content: dict[str, Any]) -> str | None:
    m = content.get("match") or {}
    return m.get("sys_object_id_prefix") if isinstance(m, dict) else None


async def list_profiles(session: AsyncSession, p: Principal) -> list[ProfileSummary]:
    p.require("profiles.read")
    active = await _active(session)
    counts = dict(
        (
            await session.execute(
                select(ReadProfile.profile_key, func.max(ReadProfile.version)).group_by(
                    ReadProfile.profile_key
                )
            )
        )
        .tuples()
        .all()
    )
    out = []
    for key in sorted(counts):
        cur = active.get(key)
        content = cur.content if cur else {}
        yaml_text = cur.content_yaml if cur else ""
        out.append(
            ProfileSummary(
                key=key,
                description=content.get("description"),
                active_version=cur.version if cur else None,
                latest_version=counts[key],
                source=cur.source if cur else None,
                sys_object_id_prefix=_match(content),
                placeholders=yaml_text.count("PREENCHER_PELO_WALK"),
                updated_at=cur.created_at if cur else None,
            )
        )
    return out


async def get_profile(session: AsyncSession, p: Principal, key: str) -> ProfileDetail:
    p.require("profiles.read")
    rows = list(
        (
            await session.execute(
                select(ReadProfile, User.name)
                .outerjoin(User, User.id == ReadProfile.created_by)
                .where(ReadProfile.profile_key == key)
                .order_by(ReadProfile.version.desc())
            )
        ).tuples()
    )
    if not rows:
        raise not_found("Perfil")
    active = next((r for r, _ in rows if r.active), rows[0][0])
    return ProfileDetail(
        key=key,
        active_version=active.version if active.active else None,
        yaml=active.content_yaml,
        versions=[
            ProfileVersionOut(
                version=r.version,
                source=r.source,
                active=r.active,
                created_at=r.created_at,
                created_by=name,
                notes=r.notes,
            )
            for r, name in rows
        ],
    )


async def get_version_yaml(session: AsyncSession, p: Principal, key: str, version: int) -> str:
    p.require("profiles.read")
    row = (
        await session.execute(
            select(ReadProfile.content_yaml).where(
                ReadProfile.profile_key == key, ReadProfile.version == version
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Versão do perfil")
    return row


async def publish(session: AsyncSession, p: Principal, text: str, notes: str | None) -> ProfileDetail:
    """New version from the portal: validated, active, distributed to every collector."""
    p.require("profiles.write")
    try:
        doc = parse_yaml(text)
    except ProfileError as exc:
        raise bad_request("invalid_profile", str(exc)) from exc
    key = str(doc["id"])
    top = (
        await session.execute(select(func.max(ReadProfile.version)).where(ReadProfile.profile_key == key))
    ).scalar_one()
    version = (top or 0) + 1
    content = {k: v for k, v in doc.items() if k != "version"}
    await session.execute(update(ReadProfile).where(ReadProfile.profile_key == key).values(active=False))
    session.add(
        ReadProfile(
            profile_key=key,
            version=version,
            content_yaml=text,
            content={**content, "version": version},
            source="portal",
            notes=(notes or "").strip() or None,
            created_by=p.user_id,
        )
    )
    await session.flush()
    await bump_all_configs(session)
    await audit.record(
        session,
        p,
        action="publish",
        entity="read_profile",
        entity_id=None,
        reseller_id=None,
        after={"profile": key, "version": version, "notes": notes},
    )
    return await get_profile(session, p, key)


async def activate(session: AsyncSession, p: Principal, key: str, version: int) -> ProfileDetail:
    """Volta para uma versão anterior (ou avança para outra): só ela fica ativa."""
    p.require("profiles.write")
    target = (
        await session.execute(
            select(ReadProfile).where(ReadProfile.profile_key == key, ReadProfile.version == version)
        )
    ).scalar_one_or_none()
    if target is None:
        raise not_found("Versão do perfil")
    await session.execute(update(ReadProfile).where(ReadProfile.profile_key == key).values(active=False))
    target.active = True
    await session.flush()
    await bump_all_configs(session)
    await audit.record(
        session,
        p,
        action="activate",
        entity="read_profile",
        entity_id=target.id,
        reseller_id=None,
        after={"profile": key, "version": version},
    )
    return await get_profile(session, p, key)


# ----------------------------------------------------------------------------- walks


@dataclass(frozen=True)
class _Row:
    oid: str
    kind: str
    value: str


def _decode(kind: str, value: str) -> str:
    if kind == "4x":
        try:
            raw = bytes.fromhex(value)
        except ValueError:
            return value
        text = raw.decode("utf-8", errors="replace")
        printable = sum(ch.isprintable() for ch in text)
        return text if text and printable / len(text) > 0.9 else value  # noqa: PLR2004
    return value


def _load(path: Path) -> list[_Row]:
    rows: list[_Row] = []
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("|", 2)
            if len(parts) != 3:  # noqa: PLR2004
                continue
            oid, kind, value = parts
            rows.append(_Row(oid, kind, _decode(kind, value)))
    return rows


def _oid_key(oid: str) -> tuple[int, ...]:
    return tuple(int(x) for x in oid.split(".") if x.isdigit())


async def walk_tree(
    session: AsyncSession,
    p: Principal,
    walk_id: uuid.UUID,
    *,
    value: str | None,
    q: str | None,
    prefix: str | None,
    offset: int,
    limit: int,
) -> WalkTree:
    """Walk rows filtered by exact value (the counter printed on the machine's report), by text or by
    OID prefix. Numeric values match ignoring thousand separators (100.150 = 100150)."""
    walk = await get_walk(session, p, walk_id)
    rows = await asyncio.to_thread(_load, Path(walk.file_path))
    target = re.sub(r"[.\s]", "", value) if value else None
    needle = q.lower() if q else None
    pref = prefix.strip(".") if prefix else None
    hits = [
        r
        for r in rows
        if (target is None or r.value.strip() == value or (target.isdigit() and r.value.strip() == target))
        and (needle is None or needle in r.oid or needle in r.value.lower())
        and (pref is None or r.oid == pref or r.oid.startswith(pref + "."))
    ]
    hits.sort(key=lambda r: _oid_key(r.oid))
    return WalkTree(
        walk_id=walk.id,
        ip=walk.ip,
        total=len(hits),
        oid_count=len(rows),
        items=[WalkRow(oid=r.oid, type=r.kind, value=r.value) for r in hits[offset : offset + limit]],
    )


async def save_fixture(session: AsyncSession, p: Principal, walk_id: uuid.UUID, body: FixtureIn) -> str:
    """Grava o walk como gravação real de teste (/profiles/recordings/real): passa a rodar no CI (6.6).
    Os valores da folha de contadores, quando informados, viram <nome>.expected.json e o teste do motor
    (agent/internal/profile/real_test.go) compara contador a contador."""
    p.require("profiles.write")
    name = body.name
    if not FIXTURE_NAME.match(name):
        raise bad_request(
            "invalid_fixture_name", "Nome: letras minúsculas, números, _ e - (ex.: canon_ir-adv_c5540)"
        )
    walk = await get_walk(session, p, walk_id)
    target = FIXTURES_DIR / f"{name}.snmprec"
    if target.exists():
        raise bad_request("fixture_exists", f"Já existe a gravação {target.name}")
    expected = {
        k: v
        for k, v in {"profile": body.profile, "serial": body.serial, "counters": body.counters}.items()
        if v
    }

    def _write() -> None:
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        with gzip.open(walk.file_path, "rb") as src:
            target.write_bytes(src.read())
        if expected:
            (FIXTURES_DIR / f"{name}.expected.json").write_text(
                json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

    await asyncio.to_thread(_write)
    await audit.record(
        session,
        p,
        action="save_fixture",
        entity="mib_walk",
        entity_id=walk.id,
        reseller_id=walk.reseller_id,
        after={"file": target.name, "expected": expected},
    )
    return target.name
