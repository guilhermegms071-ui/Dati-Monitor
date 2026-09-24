"""Multi-reseller isolation (PROMPT section 13): a user never sees or changes another reseller's data,
and a customer-scoped user only sees their own customer."""

import httpx
import pytest

from tests.conftest import Factory, auth, login

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_reseller_a_cannot_touch_reseller_b(client: httpx.AsyncClient, factory: Factory) -> None:
    a = await factory.tenant("Revenda A")
    b = await factory.tenant("Revenda B")
    ha = auth(await login(client, a.admin_email))
    b_user_id, _ = await factory.user(b.reseller_id, role="technician")

    reads = [
        f"/api/v1/resellers/{b.reseller_id}",
        f"/api/v1/companies/{b.company_id}",
        f"/api/v1/customers/{b.customer_id}",
        f"/api/v1/sites/{b.site_id}",
        f"/api/v1/users/{b_user_id}",
    ]
    for url in reads:
        resp = await client.get(url, headers=ha)
        assert resp.status_code == 404, url
        assert (
            await client.patch(url, json={"name": "hack", "legal_name": "hack"}, headers=ha)
        ).status_code in (403, 404), url
        assert (await client.delete(url, headers=ha)).status_code in (403, 404), url

    for url, key in (
        ("/api/v1/companies", "legal_name"),
        ("/api/v1/customers", "name"),
        ("/api/v1/sites", "name"),
        ("/api/v1/users", "email"),
        ("/api/v1/resellers", "name"),
    ):
        items = (await client.get(url, headers=ha)).json()["items"]
        values = {str(i[key]) for i in items}
        assert not any("Revenda B" in v for v in values), (url, values)
        assert not any(str(i.get("reseller_id", a.reseller_id)) == str(b.reseller_id) for i in items), url

    # Criar filhos sob pais de outra revenda.
    assert (
        await client.post(
            "/api/v1/customers", json={"company_id": str(b.company_id), "name": "X X"}, headers=ha
        )
    ).status_code == 404
    assert (
        await client.post("/api/v1/sites", json={"customer_id": str(b.customer_id), "name": "X"}, headers=ha)
    ).status_code == 404
    assert (
        await client.post(
            "/api/v1/users",
            json={
                "name": "X X",
                "email": "x@x.test",
                "role": "customer_viewer",
                "customer_id": str(b.customer_id),
            },
            headers=ha,
        )
    ).status_code == 404
    moved = await client.patch(
        f"/api/v1/customers/{a.customer_id}", json={"company_id": str(b.company_id)}, headers=ha
    )
    assert moved.status_code == 404

    # Auditoria e exportações também filtradas.
    hb = auth(await login(client, b.admin_email))
    await client.post("/api/v1/companies", json={"legal_name": "Segredo da B"}, headers=hb)
    audit = (await client.get("/api/v1/audit", headers=ha)).json()["items"]
    assert all(i["reseller_id"] in (str(a.reseller_id), None) for i in audit)
    export = (await client.get("/api/v1/customers/export", headers=ha)).content.decode("utf-8-sig")
    assert "Revenda B" not in export
    assert "Revenda A Cliente" in export


async def test_customer_scoped_user_sees_only_own_customer(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    a = await factory.tenant("Revenda A")
    ha = auth(await login(client, a.admin_email))
    other = (
        await client.post(
            "/api/v1/customers", json={"company_id": str(a.company_id), "name": "Outro Cliente"}, headers=ha
        )
    ).json()
    other_site = (
        await client.post(
            "/api/v1/sites", json={"customer_id": other["id"], "name": "Local do Outro"}, headers=ha
        )
    ).json()
    _, viewer = await factory.user(a.reseller_id, role="customer_viewer", customer_id=a.customer_id)
    hv = auth(await login(client, viewer))

    customers = (await client.get("/api/v1/customers", headers=hv)).json()["items"]
    assert [c["id"] for c in customers] == [str(a.customer_id)]
    assert (await client.get(f"/api/v1/customers/{other['id']}", headers=hv)).status_code == 404
    sites = (await client.get("/api/v1/sites", headers=hv)).json()["items"]
    assert [s["id"] for s in sites] == [str(a.site_id)]
    assert (await client.get(f"/api/v1/sites/{other_site['id']}", headers=hv)).status_code == 404
    # Sem permissão de escrita nem de usuários/empresas.
    assert (
        await client.patch(f"/api/v1/customers/{a.customer_id}", json={"name": "Z Z"}, headers=hv)
    ).status_code == 403
    assert (await client.get("/api/v1/users", headers=hv)).status_code == 403
    assert (await client.get("/api/v1/companies", headers=hv)).status_code == 403


async def test_operator_with_customer_scope_sees_own_company_only(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    a = await factory.tenant("Revenda A")
    ha = auth(await login(client, a.admin_email))
    await client.post("/api/v1/companies", json={"legal_name": "Empresa Sem Relação"}, headers=ha)
    _, scoped = await factory.user(a.reseller_id, role="operator", customer_id=a.customer_id)
    hs = auth(await login(client, scoped))
    companies = (await client.get("/api/v1/companies", headers=hs)).json()["items"]
    assert [c["id"] for c in companies] == [str(a.company_id)]
    created = await client.post(
        "/api/v1/customers", json={"company_id": str(a.company_id), "name": "Novo"}, headers=hs
    )
    assert created.status_code == 403
