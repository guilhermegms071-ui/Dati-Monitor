"""Brands (by SNMP enterprise ID) and read profiles synchronized from /profiles/*.yaml (PROMPT 6.4)."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.product import REPO_ROOT
from app.models import Brand, ReadProfile
from app.services.agents import bump_all_configs

logger = logging.getLogger(__name__)

PROFILES_DIR = REPO_ROOT / "profiles"
SCHEMA_PATH = PROFILES_DIR / "profile.schema.json"
ENTERPRISE_PREFIX = "1.3.6.1.4.1."

# Enterprise IDs (PROMPT 6.4).
BRANDS: dict[int, str] = {
    1602: "Canon",
    18334: "Konica Minolta",
    11: "HP",
    367: "Ricoh",
    1347: "Kyocera",
    253: "Xerox",
    2435: "Brother",
    236: "Samsung",
    641: "Lexmark",
    2385: "Sharp",
    1248: "Epson",
    2001: "OKI",
    1129: "Toshiba",
}


def enterprise_id(sys_object_id: str | None) -> int | None:
    if not sys_object_id:
        return None
    oid = sys_object_id.strip().lstrip(".")
    if not oid.startswith(ENTERPRISE_PREFIX):
        return None
    head = oid[len(ENTERPRISE_PREFIX) :].split(".", 1)[0]
    return int(head) if head.isdigit() else None


def brand_name(sys_object_id: str | None) -> str | None:
    ent = enterprise_id(sys_object_id)
    return BRANDS.get(ent) if ent is not None else None


async def sync_brands(session: AsyncSession) -> None:
    for ent, name in BRANDS.items():
        await session.execute(
            insert(Brand)
            .values(name=name, enterprise_id=ent, sys_object_id_prefix=f"{ENTERPRISE_PREFIX}{ent}")
            .on_conflict_do_update(
                index_elements=[Brand.name],
                set_={"enterprise_id": ent, "sys_object_id_prefix": f"{ENTERPRISE_PREFIX}{ent}"},
            )
        )


@lru_cache(maxsize=1)
def profile_schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(data)
    return data


class ProfileError(ValueError):
    pass


def _regexes(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for path in (
        ("match", "model_regex"),
        ("rules", "mono_only_models_regex"),
        ("status", "energy_saving_text_regex"),
    ):
        node: Any = doc
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, str):
            out.append(node)

    def walk(counter: dict[str, Any]) -> None:
        if isinstance(counter.get("name_regex"), str):
            out.append(counter["name_regex"])
        out.extend(x for x in counter.get("sum_names", []) if isinstance(x, str))
        for alt in counter.get("first_of", []):
            walk(alt)

    sources = doc.get("counter_sources") or [{"counters": doc.get("counters") or {}}]
    for src in sources:
        for counter in (src.get("counters") or {}).values():
            walk(counter)
    return out


def validate_profile(doc: Any) -> dict[str, Any]:
    """Validates against the shared JSON Schema and compiles every regex. Returns the document."""
    if not isinstance(doc, dict):
        raise ProfileError("o perfil precisa ser um objeto YAML/JSON")
    errors = sorted(
        jsonschema.Draft202012Validator(profile_schema()).iter_errors(doc), key=lambda e: list(e.path)
    )
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(raiz)"
        raise ProfileError(f"perfil fora do schema em {where}: {first.message}")
    for rx in _regexes(doc):
        try:
            re.compile(rx)
        except re.error as exc:
            raise ProfileError(f"regex inválida {rx!r}: {exc}") from exc
    return doc


@dataclass(frozen=True)
class SyncResult:
    created: list[str]
    unchanged: list[str]


async def sync_profiles(session: AsyncSession, directory: Path = PROFILES_DIR) -> SyncResult:
    """Loads every profiles/*.yaml; a profile whose content changed gets a new version (the previous
    versions stay for history). When anything changes, every agent refetches its configuration."""
    created: list[str] = []
    unchanged: list[str] = []
    files = await asyncio.to_thread(
        lambda: [(p, p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.yaml"))]
    )
    for path, text in files:
        try:
            doc = validate_profile(yaml.safe_load(text))
        except (yaml.YAMLError, ProfileError) as exc:
            raise ProfileError(f"{path.name}: {exc}") from exc
        key = doc["id"]
        content = {k: v for k, v in doc.items() if k != "version"}
        # Compara com a última versão vinda do ARQUIVO: uma versão publicada na tela Perfis de modelos
        # continua valendo depois do reinício, a menos que o YAML do repositório mude de verdade.
        latest_file = (
            await session.execute(
                select(ReadProfile)
                .where(ReadProfile.profile_key == key, ReadProfile.source == "file")
                .order_by(ReadProfile.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if (
            latest_file is not None
            and {k: v for k, v in latest_file.content.items() if k != "version"} == content
        ):
            unchanged.append(key)
            continue
        top = (
            await session.execute(select(func.max(ReadProfile.version)).where(ReadProfile.profile_key == key))
        ).scalar_one()
        version = (top or 0) + 1
        await session.execute(update(ReadProfile).where(ReadProfile.profile_key == key).values(active=False))
        session.add(
            ReadProfile(
                profile_key=key,
                version=version,
                content_yaml=text,
                content={**content, "version": version},
                source="file",
            )
        )
        created.append(f"{key} v{version}")
    if created:
        await session.flush()
        await bump_all_configs(session)
        logger.info("perfis de leitura atualizados: %s", ", ".join(created))
    return SyncResult(created=created, unchanged=unchanged)


async def count_profiles(session: AsyncSession) -> int:
    return (await session.execute(select(func.count(func.distinct(ReadProfile.profile_key))))).scalar_one()
