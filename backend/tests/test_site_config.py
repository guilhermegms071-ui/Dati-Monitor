"""IP ranges and SNMP credentials of a site (validation, write-only secrets, config bumps, scope)."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import SnmpCredential
from tests.agent_helpers import enrolled_agent
from tests.conftest import Factory, auth, login

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_ip_range_validation_and_crud(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    h = auth(await login(client, t.admin_email))
    url = f"/api/v1/sites/{t.site_id}/ip-ranges"
    bad = [
        {"cidr": "10.0.0.0/8"},  # grande demais
        {"cidr": "lixo"},
        {"cidr": "10.0.0.0/24", "start_ip": "10.0.0.1", "end_ip": "10.0.0.9"},  # os dois formatos
        {"start_ip": "10.0.0.9", "end_ip": "10.0.0.1"},
        {"start_ip": "10.0.0.1"},
        {"cidr": "10.0.0.0/24", "ports": [0]},
        {"cidr": "10.0.0.0/24", "exclusions": ["nada"]},
        {"cidr": "10.0.0.0/24", "exclusions": ["10.0.0.9-10.0.0.1"]},
        {},
    ]
    for body in bad:
        resp = await client.post(url, json=body, headers=h)
        assert resp.status_code == 422, body
    ok = await client.post(
        url,
        json={
            "cidr": "192.168.1.77/24",
            "exclusions": ["192.168.1.1", "192.168.1.200-192.168.1.210", "192.168.1.128/28"],
            "ports": [161, 161, 1161],
        },
        headers=h,
    )
    assert ok.status_code == 201, ok.text
    r = ok.json()
    assert r["cidr"] == "192.168.1.0/24"
    assert r["ports"] == [161, 1161]
    assert r["status"] == "approved"
    upd = await client.put(
        f"/api/v1/ip-ranges/{r['id']}", json={"start_ip": "192.168.1.10", "end_ip": "192.168.1.20"}, headers=h
    )
    assert upd.status_code == 200
    assert upd.json()["cidr"] is None
    assert upd.json()["start_ip"] == "192.168.1.10"
    assert (await client.delete(f"/api/v1/ip-ranges/{r['id']}", headers=h)).status_code == 204
    assert (await client.get(url, headers=h)).json() == []
    assert (await client.delete(f"/api/v1/ip-ranges/{r['id']}", headers=h)).status_code == 404


async def test_credentials_are_write_only_and_bump_config(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    h = auth(await login(client, t.admin_email))
    url = f"/api/v1/sites/{t.site_id}/snmp-credentials"
    for body in (
        {"version": "v2c"},
        {"version": "v3"},
        {"version": "v3", "v3_username": "u", "v3_auth_protocol": "SHA"},
        {"version": "v3", "v3_username": "u", "v3_priv_protocol": "AES", "v3_priv_password": "12345678"},
        {"version": "v3", "v3_username": "u", "v3_auth_protocol": "MD5", "v3_auth_password": "12345678"},
    ):
        assert (await client.post(url, json=body, headers=h)).status_code == 422, body
    v3 = await client.post(
        url,
        json={
            "version": "v3",
            "v3_username": "monitor",
            "v3_auth_protocol": "SHA256",
            "v3_auth_password": "senha-auth-1",
            "v3_priv_protocol": "AES256",
            "v3_priv_password": "senha-priv-1",
            "position": 2,
        },
        headers=h,
    )
    assert v3.status_code == 201, v3.text
    body = v3.json()
    assert body["has_auth_password"] is True
    assert body["has_priv_password"] is True
    assert "senha" not in v3.text
    dup = await client.post(url, json={"version": "v2c", "community": "x", "position": 2}, headers=h)
    assert dup.json()["detail"]["code"] == "position_taken"
    before = (await agent.heartbeat())["config_version"]
    upd = await client.patch(
        f"/api/v1/snmp-credentials/{body['id']}",
        json={"v3_auth_password": "outra-senha-2", "position": 3},
        headers=h,
    )
    assert upd.status_code == 200
    assert upd.json()["position"] == 3
    assert (await agent.heartbeat())["config_version"] == before + 1
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    v3cfg = next(c for c in cfg["credentials"] if c["version"] == "v3")
    assert v3cfg["v3_auth_password"] == "outra-senha-2"
    assert v3cfg["v3_priv_password"] == "senha-priv-1"
    async with sessionmaker() as s:
        row = await s.get(SnmpCredential, body["id"])
        assert row is not None
        assert row.v3_auth_password_enc is not None
        assert b"outra-senha" not in row.v3_auth_password_enc
    assert (await client.delete(f"/api/v1/snmp-credentials/{body['id']}", headers=h)).status_code == 204
    assert (await client.delete(f"/api/v1/snmp-credentials/{body['id']}", headers=h)).status_code == 404
    assert (
        await client.patch(f"/api/v1/snmp-credentials/{body['id']}", json={}, headers=h)
    ).status_code == 404
    async with sessionmaker() as s:
        remaining = (await s.execute(select(SnmpCredential))).scalars().all()
        assert remaining == []  # o local do factory não tem credencial padrão (criado direto no banco)


async def test_site_config_scope(client: httpx.AsyncClient, factory: Factory) -> None:
    a = await factory.tenant("Revenda A")
    b = await factory.tenant("Revenda B")
    hb = auth(await login(client, b.admin_email))
    assert (await client.get(f"/api/v1/sites/{a.site_id}/ip-ranges", headers=hb)).status_code == 404
    assert (
        await client.post(
            f"/api/v1/sites/{a.site_id}/snmp-credentials",
            json={"version": "v2c", "community": "x"},
            headers=hb,
        )
    ).status_code == 404
    ha = auth(await login(client, a.admin_email))
    r = (
        await client.post(f"/api/v1/sites/{a.site_id}/ip-ranges", json={"cidr": "10.9.9.0/24"}, headers=ha)
    ).json()
    assert (
        await client.put(f"/api/v1/ip-ranges/{r['id']}", json={"cidr": "10.9.8.0/24"}, headers=hb)
    ).status_code == 404
    _, viewer = await factory.user(a.reseller_id, role="customer_viewer", customer_id=a.customer_id)
    hv = auth(await login(client, viewer))
    assert (await client.get(f"/api/v1/sites/{a.site_id}/ip-ranges", headers=hv)).status_code == 200
    assert (
        await client.post(f"/api/v1/sites/{a.site_id}/ip-ranges", json={"cidr": "10.9.7.0/24"}, headers=hv)
    ).status_code == 403
