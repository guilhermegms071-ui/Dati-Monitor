"""Perfis de modelos (PROMPT 6.6): versions, validation, publishing from the portal (survives the file
sync), activating an older version, the walk explorer, exporting a walk as a test recording and testing a
draft profile against a device (read_device + profile)."""

import gzip
import json
from pathlib import Path

import httpx
import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Agent, AuditLog, ReadProfile
from app.services import catalog
from app.services import profiles as profiles_svc
from tests.agent_helpers import FakeAgent
from tests.conftest import Factory, auth, login
from tests.test_commands import pending, send_command, setup

REPO_PROFILES = catalog.PROFILES_DIR


def hp_yaml(serial_note: str = "série") -> str:
    doc = yaml.safe_load((REPO_PROFILES / "hp.yaml").read_text(encoding="utf-8"))
    doc["identity"]["serial"][1]["note"] = serial_note
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


async def config_version(sessionmaker: async_sessionmaker[AsyncSession], agent: FakeAgent) -> int:
    async with sessionmaker() as s:
        row = await s.get(Agent, agent.agent_id)
        assert row is not None
        return row.config_version


async def test_list_and_detail(client: httpx.AsyncClient, factory: Factory) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    listed = (await client.get("/api/v1/profiles", headers=auth(admin))).json()
    keys = {p["key"] for p in listed}
    assert {"canon", "konica-minolta", "hp", "ricoh", "generic"} <= keys
    hp = next(p for p in listed if p["key"] == "hp")
    assert hp["source"] == "file"
    assert hp["sys_object_id_prefix"] == "1.3.6.1.4.1.11"
    assert hp["placeholders"] > 0, "OIDs proprietários ainda a preencher pelo walk"
    canon = next(p for p in listed if p["key"] == "canon")
    assert canon["placeholders"] == 0
    detail = (await client.get("/api/v1/profiles/canon", headers=auth(admin))).json()
    assert detail["active_version"] == 1
    assert detail["yaml"] == (REPO_PROFILES / "canon.yaml").read_text(encoding="utf-8")
    assert [v["version"] for v in detail["versions"]] == [1]
    assert (await client.get("/api/v1/profiles/nao-existe", headers=auth(admin))).status_code == 404


async def test_validate_reports_the_first_error(client: httpx.AsyncClient, factory: Factory) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    ok = (
        await client.post("/api/v1/profiles/validate", json={"yaml": hp_yaml()}, headers=auth(admin))
    ).json()
    assert ok["ok"] is True
    assert ok["profile"]["id"] == "hp"
    bad = (
        await client.post("/api/v1/profiles/validate", json={"yaml": "id: [aberto\n"}, headers=auth(admin))
    ).json()
    assert bad["ok"] is False
    assert bad["error"].startswith("YAML inválido")
    no_match = hp_yaml().replace("sys_object_id_prefix", "prefixo_errado")
    resp = await client.post("/api/v1/profiles/validate", json={"yaml": no_match}, headers=auth(admin))
    assert resp.json()["ok"] is False
    assert "match" in resp.json()["error"]


async def test_publish_activate_and_file_sync(
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    _, admin, agent = await setup(client, factory)
    before = await config_version(sessionmaker, agent)
    body = {"yaml": hp_yaml("conferido no walk do M479"), "notes": "  série do M479  "}
    published = await client.post("/api/v1/profiles", json=body, headers=auth(admin))
    assert published.status_code == 200, published.text
    detail = published.json()
    assert detail["active_version"] == 2
    assert [(v["version"], v["source"], v["active"]) for v in detail["versions"]] == [
        (2, "portal", True),
        (1, "file", False),
    ]
    assert detail["versions"][0]["notes"] == "série do M479"
    assert detail["versions"][0]["created_by"] == "Usuário de Teste"
    assert await config_version(sessionmaker, agent) > before, "coletores recebem o perfil novo"
    cfg = (await agent.client.get("/api/agent/config", headers=agent.headers)).json()
    hp = next(p for p in cfg["profiles"] if p["id"] == "hp")
    assert hp["version"] == 2
    assert hp["identity"]["serial"][1]["note"] == "conferido no walk do M479"

    # Reinício do servidor: o arquivo do repositório não mudou, então a versão do portal continua valendo.
    async with sessionmaker() as s:
        result = await catalog.sync_profiles(s)
        await s.commit()
    assert "hp" in result.unchanged
    detail = (await client.get("/api/v1/profiles/hp", headers=auth(admin))).json()
    assert detail["active_version"] == 2

    # Voltar para a versão do arquivo.
    back = await client.post("/api/v1/profiles/hp/activate", json={"version": 1}, headers=auth(admin))
    assert back.status_code == 200, back.text
    assert back.json()["active_version"] == 1
    old = await client.get("/api/v1/profiles/hp/versions/2", headers=auth(admin))
    assert "conferido no walk do M479" in old.text
    missing = await client.post("/api/v1/profiles/hp/activate", json={"version": 9}, headers=auth(admin))
    assert missing.status_code == 404

    # Se o YAML do repositório mudar de verdade, ele vira a versão seguinte (3).
    for f in REPO_PROFILES.glob("*.yaml"):
        (tmp_path / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "hp.yaml").write_text(hp_yaml("mudou no repositório"), encoding="utf-8")
    async with sessionmaker() as s:
        result = await catalog.sync_profiles(s, tmp_path)
        await s.commit()
    assert result.created == ["hp v3"]
    detail = (await client.get("/api/v1/profiles/hp", headers=auth(admin))).json()
    assert (detail["active_version"], detail["versions"][0]["source"]) == (3, "file")

    async with sessionmaker() as s:
        actions = set(
            (await s.execute(select(AuditLog.action).where(AuditLog.entity == "read_profile"))).scalars()
        )
    assert actions == {"publish", "activate"}


async def test_publish_rejects_invalid_and_needs_permission(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    bad = await client.post("/api/v1/profiles", json={"yaml": "id: hp\nversion: 1\n"}, headers=auth(admin))
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "invalid_profile"
    _, operator_email = await factory.user(tenant.reseller_id, role="operator")
    operator = await login(client, operator_email)
    assert (await client.get("/api/v1/profiles", headers=auth(operator))).status_code == 200
    resp = await client.post("/api/v1/profiles", json={"yaml": hp_yaml()}, headers=auth(operator))
    assert resp.status_code == 403
    resp = await client.post("/api/v1/profiles/hp/activate", json={"version": 1}, headers=auth(operator))
    assert resp.status_code == 403
    _, viewer_email = await factory.user(tenant.reseller_id, role="customer_viewer")
    viewer = await login(client, viewer_email)
    assert (await client.get("/api/v1/profiles", headers=auth(viewer))).status_code == 403


WALK = (
    "1.3.6.1.2.1.1.1.0|4|HP LaserJet M479\n"
    "1.3.6.1.2.1.1.2.0|6|1.3.6.1.4.1.11.2.3.9.1\n"
    "1.3.6.1.2.1.43.10.2.1.4.1.1|65|100150\n"
    "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.5.0|65|61000\n"
    "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.6.0|65|39150\n"
    "1.3.6.1.4.1.11.2.3.9.4.2.1.1.3.3.0|4x|434e42524b3132333435\n"
    "1.3.6.1.4.1.11.2.3.9.4.2.1.1.3.10.0|4x|00ff01\n"
)


async def uploaded_walk(client: httpx.AsyncClient, admin: str, agent: FakeAgent) -> str:
    cmd = (await send_command(client, admin, agent.agent_id, "mib_walk", {"ip": "192.168.0.60"})).json()
    await pending(agent)
    up = await client.post(
        f"/api/agent/uploads/mib-walk?command_id={cmd['id']}",
        content=gzip.compress(WALK.encode()),
        headers={**agent.headers, "Content-Type": "application/gzip"},
    )
    assert up.status_code == 200, up.text
    walk_id: str = up.json()["id"]
    listed = (await client.get("/api/v1/mib-walks", headers=auth(admin))).json()
    assert [(w["id"], w["port"]) for w in listed] == [(walk_id, 161)], "a porta do walk fica guardada"
    return walk_id


async def test_walk_explorer_finds_the_counter_sheet_value(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    _, admin, agent = await setup(client, factory)
    walk_id = await uploaded_walk(client, admin, agent)
    url = f"/api/v1/mib-walks/{walk_id}/tree"
    full = (await client.get(url, headers=auth(admin))).json()
    assert (full["total"], full["oid_count"], full["ip"]) == (7, 7, "192.168.0.60")
    # Valor da folha de contadores digitado com separador de milhar.
    hits = (await client.get(url, params={"value": "100.150"}, headers=auth(admin))).json()
    assert [r["oid"] for r in hits["items"]] == ["1.3.6.1.2.1.43.10.2.1.4.1.1"]
    # Texto em hexadecimal aparece legível; binário fica em hexadecimal.
    serial = (await client.get(url, params={"q": "cnbrk"}, headers=auth(admin))).json()
    assert serial["items"][0]["value"] == "CNBRK12345"
    binary = (await client.get(url, params={"q": "00ff01"}, headers=auth(admin))).json()
    assert binary["total"] == 1
    # Sub-árvore proprietária, ordenada numericamente e paginada.
    sub = (
        await client.get(
            url, params={"prefix": "1.3.6.1.4.1.11.2.3.9.4.2.1", "limit": 2, "offset": 1}, headers=auth(admin)
        )
    ).json()
    assert sub["total"] == 4
    assert [r["oid"] for r in sub["items"]] == [
        "1.3.6.1.4.1.11.2.3.9.4.2.1.1.3.10.0",
        "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.5.0",
    ]
    other = await factory.tenant("Outra empresa")
    stranger = await login(client, other.admin_email)
    assert (await client.get(url, headers=auth(stranger))).status_code == 404


async def test_save_walk_as_real_recording(
    client: httpx.AsyncClient, factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profiles_svc, "FIXTURES_DIR", tmp_path / "real")
    _, admin, agent = await setup(client, factory)
    walk_id = await uploaded_walk(client, admin, agent)
    url = f"/api/v1/mib-walks/{walk_id}/fixture"
    bad = await client.post(url, json={"name": "HP M479!"}, headers=auth(admin))
    assert bad.status_code == 400
    body = {"name": "hp_m479", "profile": "hp", "serial": "CNBRK12345", "counters": {"total": 100150}}
    ok = await client.post(url, json=body, headers=auth(admin))
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"file": "hp_m479.snmprec"}
    assert (tmp_path / "real" / "hp_m479.snmprec").read_text(encoding="utf-8") == WALK
    expected = json.loads((tmp_path / "real" / "hp_m479.expected.json").read_text(encoding="utf-8"))
    assert expected == {"profile": "hp", "serial": "CNBRK12345", "counters": {"total": 100150}}
    again = await client.post(url, json={"name": "hp_m479"}, headers=auth(admin))
    assert again.status_code == 400
    assert again.json()["detail"]["code"] == "fixture_exists"


async def test_read_device_with_draft_profile(client: httpx.AsyncClient, factory: Factory) -> None:
    _, admin, agent = await setup(client, factory)
    draft = yaml.safe_load(hp_yaml("rascunho"))
    cmd = await send_command(
        client, admin, agent.agent_id, "read_device", {"ip": "192.168.0.60", "profile": draft}
    )
    assert cmd.status_code == 201, cmd.text
    delivered = await pending(agent)
    assert delivered[0]["params"]["profile"]["identity"]["serial"][1]["note"] == "rascunho"
    broken = {**draft, "counter_sources": "nada"}
    resp = await send_command(
        client, admin, agent.agent_id, "read_device", {"ip": "192.168.0.60", "profile": broken}
    )
    assert resp.status_code == 400
    async with factory.maker() as s:
        assert (await s.execute(select(ReadProfile).where(ReadProfile.profile_key == "hp"))).scalars().all()
