"""User management: role hierarchy, customer scope, self-protection, admin resets, audit listing/export."""

import httpx
import pytest

from tests.conftest import Factory, auth, login

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_create_user_with_temporary_password_must_change(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant()
    h = auth(await login(client, t.admin_email))
    created = await client.post(
        "/api/v1/users",
        json={"name": "Técnica Ana", "email": "Ana@Daticopy.test", "role": "technician"},
        headers=h,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["user"]["email"] == "ana@daticopy.test"
    temp = body["temporary_password"]
    assert temp
    resp = await client.post("/api/v1/auth/login", json={"email": "ana@daticopy.test", "password": temp})
    assert resp.json()["limited"] == "password_change_required"
    dup = await client.post(
        "/api/v1/users", json={"name": "Outra", "email": "ana@daticopy.test", "role": "operator"}, headers=h
    )
    assert dup.status_code == 409


async def test_role_hierarchy_and_customer_scope_rules(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    h = auth(await login(client, t.admin_email))
    superadmin = await client.post(
        "/api/v1/users", json={"name": "S S", "email": "s@x.test", "role": "superadmin"}, headers=h
    )
    assert superadmin.status_code == 403
    viewer_no_customer = await client.post(
        "/api/v1/users", json={"name": "V V", "email": "v@x.test", "role": "customer_viewer"}, headers=h
    )
    assert viewer_no_customer.json()["detail"]["code"] == "customer_required"
    admin_with_customer = await client.post(
        "/api/v1/users",
        json={
            "name": "A A",
            "email": "a2@x.test",
            "role": "reseller_admin",
            "customer_id": str(t.customer_id),
        },
        headers=h,
    )
    assert admin_with_customer.json()["detail"]["code"] == "customer_not_allowed"
    viewer = await client.post(
        "/api/v1/users",
        json={
            "name": "Cliente",
            "email": "c@x.test",
            "role": "customer_viewer",
            "customer_id": str(t.customer_id),
            "password": "Senha-Do-Cliente-1",
        },
        headers=h,
    )
    assert viewer.status_code == 201
    roles = (await client.get("/api/v1/roles", headers=h)).json()
    assert [r["code"] for r in roles] == ["reseller_admin", "operator", "technician", "customer_viewer"]

    _, oper = await factory.user(t.reseller_id, role="operator")
    ho = auth(await login(client, oper))
    assert (
        await client.post(
            "/api/v1/users", json={"name": "X X", "email": "z@x.test", "role": "technician"}, headers=ho
        )
    ).status_code == 403


async def test_admin_cannot_lock_self_out(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    token = await login(client, t.admin_email)
    me = (await client.get("/api/v1/auth/me", headers=auth(token))).json()
    h = auth(token)
    off = await client.patch(f"/api/v1/users/{me['id']}", json={"active": False}, headers=h)
    assert off.json()["detail"]["code"] == "cannot_change_self"
    demote = await client.patch(f"/api/v1/users/{me['id']}", json={"role": "operator"}, headers=h)
    assert demote.json()["detail"]["code"] == "cannot_change_self"
    delete = await client.delete(f"/api/v1/users/{me['id']}", headers=h)
    assert delete.json()["detail"]["code"] == "cannot_delete_self"


async def test_update_reset_and_delete_user(
    client: httpx.AsyncClient, factory: Factory, mail_catcher: object
) -> None:
    t = await factory.tenant()
    h = auth(await login(client, t.admin_email))
    uid, email = await factory.user(t.reseller_id, role="technician")
    upd = await client.patch(
        f"/api/v1/users/{uid}",
        json={"name": "Novo Nome", "role": "operator", "customer_id": str(t.customer_id)},
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["role_code"] == "operator"
    assert upd.json()["customer_id"] == str(t.customer_id)
    cleared = await client.patch(f"/api/v1/users/{uid}", json={"clear_customer": True}, headers=h)
    assert cleared.json()["customer_id"] is None

    reset = await client.post(f"/api/v1/users/{uid}/reset-password", json={"mode": "temporary"}, headers=h)
    temp = reset.json()["temporary_password"]
    assert temp
    login_resp = await client.post("/api/v1/auth/login", json={"email": email, "password": temp})
    assert login_resp.json()["limited"] == "password_change_required"

    by_mail = await client.post(f"/api/v1/users/{uid}/reset-password", json={"mode": "email"}, headers=h)
    assert by_mail.json()["temporary_password"] is None
    assert any(m["to"] == [email] for m in mail_catcher.store.list())  # type: ignore[attr-defined]

    assert (await client.post(f"/api/v1/users/{uid}/reset-totp", headers=h)).status_code == 200
    assert (await client.delete(f"/api/v1/users/{uid}", headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/users/{uid}", headers=h)).status_code == 404
    listed = (await client.get("/api/v1/users", headers=h)).json()["items"]
    assert email not in {u["email"] for u in listed}


async def test_audit_list_filters_and_export(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    token = await login(client, t.admin_email)
    h = auth(token)
    await client.post("/api/v1/companies", json={"legal_name": "Empresa Auditada"}, headers=h)
    page = (await client.get("/api/v1/audit", params={"entity": "company"}, headers=h)).json()
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["action"] == "create"
    assert item["user_email"] == t.admin_email
    assert item["after"]["legal_name"] == "Empresa Auditada"
    export = await client.get(
        "/api/v1/audit/export", params={"format": "csv", "entity": "company"}, headers=h
    )
    lines = export.content.decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 2
    assert t.admin_email in lines[1]


async def test_admin_cannot_edit_higher_role(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    sid, _ = await factory.user(t.reseller_id, role="superadmin")
    h = auth(await login(client, t.admin_email))
    resp = await client.patch(f"/api/v1/users/{sid}", json={"name": "Hack"}, headers=h)
    assert resp.status_code == 403
