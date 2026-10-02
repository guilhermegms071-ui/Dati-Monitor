"""API do ERP (PROMPT 7 / 15 item 11): integration tokens (hash only, revocable, audited), readings with the
same rules as the reports, cutoff reading and devices, all scoped to the token's company."""

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog, ErpToken
from tests.conftest import Factory, auth, login
from tests.test_reports import SERIAL, build_park


async def new_token(client: httpx.AsyncClient, admin: str, name: str = "Dataclassic") -> str:
    resp = await client.post("/api/v1/erp-tokens", json={"name": name}, headers=auth(admin))
    assert resp.status_code == 201, resp.text
    token: str = resp.json()["token"]
    return token


def erp(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_tokens_are_shown_once_and_revocable(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    created = (
        await client.post("/api/v1/erp-tokens", json={"name": "Dataclassic"}, headers=auth(admin))
    ).json()
    token = created["token"]
    assert token.startswith("dmerp_")
    assert token.startswith(created["token_prefix"])
    listed = (await client.get("/api/v1/erp-tokens", headers=auth(admin))).json()
    assert "token" not in listed[0]
    assert listed[0]["created_by"] == "Usuário de Teste"
    async with sessionmaker() as s:
        row = (await s.execute(select(ErpToken))).scalar_one()
        assert token not in (row.token_hash, row.token_prefix), "o banco guarda só o hash"

    ok = await client.get("/api/erp/v1/devices", headers=erp(token))
    assert ok.status_code == 200
    listed = (await client.get("/api/v1/erp-tokens", headers=auth(admin))).json()
    assert listed[0]["last_used_at"] is not None

    revoked = await client.post(f"/api/v1/erp-tokens/{created['id']}/revoke", headers=auth(admin))
    assert revoked.json()["revoked_at"] is not None
    denied = await client.get("/api/erp/v1/devices", headers=erp(token))
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "erp_token_invalid"
    assert (await client.get("/api/erp/v1/devices")).status_code == 401
    # O token do portal não serve na API do ERP (e vice-versa).
    assert (await client.get("/api/erp/v1/devices", headers=auth(admin))).status_code == 401
    assert (await client.get("/api/v1/erp-tokens", headers=erp(token))).status_code == 401

    _, viewer_email = await factory.user(tenant.reseller_id, role="technician")
    tech = await login(client, viewer_email)
    assert (
        await client.post("/api/v1/erp-tokens", json={"name": "x1"}, headers=auth(tech))
    ).status_code == 403
    async with sessionmaker() as s:
        actions = set(
            (await s.execute(select(AuditLog.action).where(AuditLog.entity == "erp_token"))).scalars()
        )
    assert actions == {"create", "revoke"}


async def test_readings_cutoff_and_devices(client: httpx.AsyncClient, factory: Factory) -> None:
    park = await build_park(client, factory)
    token = await new_token(client, park.admin)
    period = {"from": park.prev.isoformat(), "to": park.last_day.isoformat()}

    page = (await client.get("/api/erp/v1/readings", params=period, headers=erp(token))).json()
    totals = [r["total"] for r in page["items"] if r["serial"] == SERIAL]
    assert totals == [1000, 1500, 1600], "a regressão (900) fica de fora"
    assert page["next_cursor"] is None
    assert {r["customer_erp_code"] for r in page["items"]} == {"E-Revenda A"}

    # Paginação por cursor: 1 por página, mesma ordem.
    seen: list[int] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {**period, "limit": 1}
        if cursor:
            params["cursor"] = cursor
        resp = (await client.get("/api/erp/v1/readings", params=params, headers=erp(token))).json()
        seen += [r["total"] for r in resp["items"] if r["serial"] == SERIAL]
        cursor = resp["next_cursor"]
        if cursor is None:
            break
    assert seen == [1000, 1500, 1600]

    cut = (
        await client.get("/api/erp/v1/cutoff", params={"date": park.day(16).isoformat()}, headers=erp(token))
    ).json()
    item = next(i for i in cut["items"] if i["serial"] == SERIAL)
    assert (item["total"], item["mono"], item["color"]) == (1500, 1100, 400)

    devices = (
        await client.get(
            "/api/erp/v1/devices", params={"customer_erp_code": "E-Revenda A"}, headers=erp(token)
        )
    ).json()
    assert {d["serial"] for d in devices["items"]} == {SERIAL, "RPT0002"}
    assert devices["items"][0]["customer_name"] == "Revenda A Cliente"
    missing = await client.get("/api/erp/v1/devices", params={"customer_erp_code": "NAO"}, headers=erp(token))
    assert missing.status_code == 404
    bad = await client.get(
        "/api/erp/v1/readings", params={"from": "2026-05-10", "to": "2026-05-01"}, headers=erp(token)
    )
    assert bad.json()["detail"]["code"] == "invalid_period"
    bad = await client.get("/api/erp/v1/readings", params={**period, "cursor": "lixo"}, headers=erp(token))
    assert bad.json()["detail"]["code"] == "invalid_cursor"

    # O token de outra empresa não enxerga estes equipamentos.
    other = await factory.tenant("Outra empresa")
    other_admin = await login(client, other.admin_email)
    other_token = await new_token(client, other_admin)
    empty = (await client.get("/api/erp/v1/devices", headers=erp(other_token))).json()
    assert empty["items"] == []
    none = (await client.get("/api/erp/v1/readings", params=period, headers=erp(other_token))).json()
    assert none["items"] == []


async def test_openapi_documents_the_erp_api(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    for path in ("/api/erp/v1/readings", "/api/erp/v1/cutoff", "/api/erp/v1/devices"):
        assert path in spec["paths"]
        assert spec["paths"][path]["get"]["security"] == [{"ErpToken": []}]
    assert spec["components"]["securitySchemes"]["ErpToken"]["scheme"] == "bearer"
