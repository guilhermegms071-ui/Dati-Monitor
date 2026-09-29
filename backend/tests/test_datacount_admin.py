"""Portal additions of the Datacount audit: permission matrix (16.14), custom fields and billing fields
(16.7), toner thresholds (16.5), full site address (16.9) and collector options (16.10)."""

from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import MATRIX_PERMISSIONS, ROLE_PERMISSIONS
from app.models import Device, Site
from tests.agent_helpers import enrolled_agent
from tests.conftest import Factory, Tenant, auth, login


async def _device_id(client: httpx.AsyncClient, t: Tenant, serial: str = "SN-ADM") -> str:
    agent = await enrolled_agent(client, t, name=f"Coletor {serial}")
    await agent.send([agent.reading(serial, {"total": 100, "mono": 60, "color": 40})])
    admin = await login(client, t.admin_email)
    park = (await client.get("/api/v1/park", params={"serial": serial}, headers=auth(admin))).json()
    device_id: str = park["items"][0]["id"]
    return device_id


# ----------------------------------------------------------------------------- matriz de permissões


async def test_permission_matrix_changes_what_the_role_can_do(
    client: httpx.AsyncClient, factory: Factory
) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    device_id = await _device_id(client, t)
    _, op_email = await factory.user(t.reseller_id, role="operator")
    op = await login(client, op_email)

    matrix = (await client.get("/api/v1/permissions/matrix", headers=auth(admin))).json()
    assert [m["name"] for m in matrix["modules"]] == [
        "Clientes",
        "Coletores",
        "Equipamentos",
        "Relatórios",
        "Integração",
        "Usuários",
    ]
    assert matrix["actions"] == {
        "read": "Consultar",
        "create": "Incluir",
        "update": "Alterar",
        "delete": "Excluir",
    }
    operator = next(r for r in matrix["roles"] if r["role"] == "operator")
    assert operator["customized"] is False
    assert set(operator["permissions"]) == ROLE_PERMISSIONS["operator"] & MATRIX_PERMISSIONS

    patch = {"notes": "ok"}
    assert (
        await client.patch(f"/api/v1/devices/{device_id}", json=patch, headers=auth(op))
    ).status_code == 200
    # A revenda tira "Alterar equipamentos" do operador: vale na próxima requisição (sem novo login).
    reduced = sorted(set(operator["permissions"]) - {"devices.update"})
    resp = await client.put(
        "/api/v1/permissions/matrix/operator", json={"permissions": reduced}, headers=auth(admin)
    )
    assert resp.status_code == 200, resp.text
    assert next(r for r in resp.json()["roles"] if r["role"] == "operator")["customized"] is True
    assert (
        await client.patch(f"/api/v1/devices/{device_id}", json=patch, headers=auth(op))
    ).status_code == 403
    me = (await client.get("/api/v1/auth/me", headers=auth(op))).json()
    assert "devices.update" not in me["permissions"]
    assert "agents.command" in me["permissions"]  # permissões especiais seguem o papel

    # Outra revenda continua com o padrão.
    other = await factory.tenant("Revenda B")
    _, op_b_email = await factory.user(other.reseller_id, role="operator")
    me_b = (await client.get("/api/v1/auth/me", headers=auth(await login(client, op_b_email)))).json()
    assert "devices.update" in me_b["permissions"]

    # Voltar ao padrão.
    await client.put("/api/v1/permissions/matrix/operator", json={"permissions": None}, headers=auth(admin))
    assert (
        await client.patch(f"/api/v1/devices/{device_id}", json=patch, headers=auth(op))
    ).status_code == 200


async def test_matrix_limits(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    _, op_email = await factory.user(t.reseller_id, role="operator")
    op = await login(client, op_email)
    # Papel Cliente só consulta: não recebe Incluir/Alterar/Excluir.
    bad = await client.put(
        "/api/v1/permissions/matrix/customer_viewer",
        json={"permissions": ["devices.read", "devices.update"]},
        headers=auth(admin),
    )
    assert (bad.status_code, bad.json()["detail"]["permissions"]) == (400, ["devices.update"])
    ok = await client.put(
        "/api/v1/permissions/matrix/customer_viewer",
        json={"permissions": ["devices.read", "supplies.monitor"]},
        headers=auth(admin),
    )
    assert ok.status_code == 200
    # Administrador não tem matriz editável (evita a revenda se trancar fora); operador não edita a matriz.
    locked = await client.put(
        "/api/v1/permissions/matrix/reseller_admin", json={"permissions": []}, headers=auth(admin)
    )
    assert locked.status_code == 400
    denied = await client.put(
        "/api/v1/permissions/matrix/operator", json={"permissions": []}, headers=auth(op)
    )
    assert denied.status_code == 403
    assert (await client.get("/api/v1/permissions/matrix", headers=auth(op))).status_code == 403


# ----------------------------------------------------------------------------- cadastro do equipamento


async def test_custom_fields_and_billing_fields(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    device_id = await _device_id(client, t)
    for definition in (
        {"key": "contrato", "label": "Contrato", "field_type": "text", "position": 1},
        {"key": "vencimento", "label": "Vencimento", "field_type": "date", "position": 2},
        {"key": "valor_hora", "label": "Valor/hora técnica", "field_type": "number", "position": 3},
    ):
        resp = await client.post("/api/v1/custom-fields", json=definition, headers=auth(admin))
        assert resp.status_code == 201, resp.text
    dup = await client.post(
        "/api/v1/custom-fields", json={"key": "contrato", "label": "X"}, headers=auth(admin)
    )
    assert dup.status_code == 409
    bad_key = await client.post(
        "/api/v1/custom-fields", json={"key": "Com Espaço", "label": "X"}, headers=auth(admin)
    )
    assert bad_key.status_code == 422

    body: dict[str, Any] = {
        "asset_tag": "PAT-0001",
        "alt_serial": "ALT-99",
        "franchise_value": "450.00",
        "franchise_pages_mono": 5000,
        "franchise_pages_color": 1000,
        "overage_price_mono": "0.0450",
        "overage_price_color": "0.3500",
        "custom_fields": {"contrato": "CT-2026/15", "vencimento": "2026-12-31", "valor_hora": "120,50"},
    }
    resp = await client.patch(f"/api/v1/devices/{device_id}", json=body, headers=auth(admin))
    assert resp.status_code == 200, resp.text
    detail = (await client.get(f"/api/v1/devices/{device_id}", headers=auth(admin))).json()
    assert (detail["alt_serial"], detail["franchise_value"], detail["overage_price_color"]) == (
        "ALT-99",
        "450.00",
        "0.3500",
    )
    assert detail["custom_fields"] == {
        "contrato": "CT-2026/15",
        "vencimento": "2026-12-31",
        "valor_hora": 120.5,
    }

    wrong_date = {"custom_fields": {"vencimento": "31/12/2026"}}
    resp = await client.patch(f"/api/v1/devices/{device_id}", json=wrong_date, headers=auth(admin))
    assert (resp.status_code, resp.json()["detail"]["field"]) == (400, "vencimento")
    unknown = await client.patch(
        f"/api/v1/devices/{device_id}", json={"custom_fields": {"nao_existe": "x"}}, headers=auth(admin)
    )
    assert unknown.status_code == 400
    negative = await client.patch(
        f"/api/v1/devices/{device_id}", json={"franchise_pages_mono": -1}, headers=auth(admin)
    )
    assert negative.status_code == 422
    # Valor vazio remove o campo.
    await client.patch(
        f"/api/v1/devices/{device_id}", json={"custom_fields": {"contrato": ""}}, headers=auth(admin)
    )
    detail = (await client.get(f"/api/v1/devices/{device_id}", headers=auth(admin))).json()
    assert "contrato" not in detail["custom_fields"]
    # Setor em lote: deixa de seguir o sysLocation.
    bulk = await client.post(
        "/api/v1/devices/bulk",
        json={"device_ids": [device_id], "action": "update", "sector": "Recepção"},
        headers=auth(admin),
    )
    assert bulk.json()["changed"] == 1
    async with sessionmaker() as s:
        device = await s.get(Device, device_id)
        assert device is not None
        assert (device.sector, device.sector_from_snmp) == ("Recepção", False)
    # Só quem edita configurações define campos (operador não).
    _, op_email = await factory.user(t.reseller_id, role="operator")
    op = await login(client, op_email)
    assert (
        await client.post("/api/v1/custom-fields", json={"key": "x", "label": "X"}, headers=auth(op))
    ).status_code == 403
    assert len((await client.get("/api/v1/custom-fields", headers=auth(op))).json()) == 3


async def test_toner_thresholds_per_customer_and_device(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    device_id = await _device_id(client, t)
    customer = (await client.get(f"/api/v1/customers/{t.customer_id}", headers=auth(admin))).json()
    assert (customer["toner_monitoring"], customer["toner_thresholds"]) == (
        True,
        {"black": 10, "cyan": 10, "magenta": 10, "yellow": 10},
    )
    resp = await client.patch(
        f"/api/v1/customers/{t.customer_id}",
        json={"toner_thresholds": {"black": 15, "cyan": 5, "magenta": 5, "yellow": 5}},
        headers=auth(admin),
    )
    assert resp.json()["toner_thresholds"]["black"] == 15
    over = await client.patch(
        f"/api/v1/customers/{t.customer_id}", json={"toner_thresholds": {"black": 150}}, headers=auth(admin)
    )
    assert over.status_code == 422

    individual = await client.patch(
        f"/api/v1/devices/{device_id}", json={"toner_mode": "individual"}, headers=auth(admin)
    )
    assert individual.status_code == 400  # individual exige limiares próprios
    resp = await client.patch(
        f"/api/v1/devices/{device_id}",
        json={
            "toner_mode": "individual",
            "toner_thresholds": {"black": 20, "cyan": 20, "magenta": 20, "yellow": 20},
        },
        headers=auth(admin),
    )
    assert resp.status_code == 200, resp.text
    detail = (await client.get(f"/api/v1/devices/{device_id}", headers=auth(admin))).json()
    assert (detail["toner_mode"], detail["toner_thresholds"]["black"]) == ("individual", 20)

    # Sem "Monitorar suprimentos" não mexe em limiar (mesmo podendo alterar o cliente).
    matrix = (await client.get("/api/v1/permissions/matrix", headers=auth(admin))).json()
    operator = next(r for r in matrix["roles"] if r["role"] == "operator")
    await client.put(
        "/api/v1/permissions/matrix/operator",
        json={"permissions": sorted(set(operator["permissions"]) - {"supplies.monitor"})},
        headers=auth(admin),
    )
    _, op_email = await factory.user(t.reseller_id, role="operator")
    op = await login(client, op_email)
    denied = await client.patch(f"/api/v1/devices/{device_id}", json={"toner_mode": "off"}, headers=auth(op))
    assert denied.status_code == 403
    denied = await client.patch(
        f"/api/v1/customers/{t.customer_id}", json={"toner_monitoring": False}, headers=auth(op)
    )
    assert denied.status_code == 403
    allowed = await client.patch(
        f"/api/v1/customers/{t.customer_id}", json={"phone": "21 99999-0000"}, headers=auth(op)
    )
    assert allowed.status_code == 200


# ----------------------------------------------------------------------------- endereço do local


async def test_site_full_address(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    body = {
        "customer_id": str(t.customer_id),
        "name": "Sede",
        "cep": "20040-002",
        "street": "Avenida Rio Branco",
        "number": "1",
        "district": "Centro",
        "city": "Rio de Janeiro",
        "state": "rj",
        "latitude": "-22.903539",
        "longitude": "-43.175003",
    }
    resp = await client.post("/api/v1/sites", json=body, headers=auth(admin))
    assert resp.status_code == 201, resp.text
    site = resp.json()
    assert (site["cep"], site["state"], site["latitude"], site["auto_activate_devices"]) == (
        "20040002",
        "RJ",
        "-22.903539",
        False,
    )
    found = (await client.get("/api/v1/sites", params={"q": "Rio Branco"}, headers=auth(admin))).json()
    assert [s["id"] for s in found["items"]] == [site["id"]]
    for bad in ({"cep": "123"}, {"state": "XX"}, {"latitude": "-91"}, {"longitude": "181"}):
        resp = await client.patch(f"/api/v1/sites/{site['id']}", json=bad, headers=auth(admin))
        assert resp.status_code == 422, bad


# ----------------------------------------------------------------------------- opções do coletor


async def test_single_hosts_and_txt_import(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    agent = await enrolled_agent(client, t)
    one = await client.post(
        f"/api/v1/sites/{t.site_id}/ip-ranges", json={"host": "Impressora-RH.local"}, headers=auth(admin)
    )
    assert (one.status_code, one.json()["host"]) == (201, "impressora-rh.local")
    two = await client.post(
        f"/api/v1/sites/{t.site_id}/ip-ranges",
        json={"host": "10.0.0.1", "cidr": "10.0.0.0/24"},
        headers=auth(admin),
    )
    assert two.status_code == 422  # uma forma só

    content = "\n".join(
        [
            "# faixas do cliente",
            "192.168.1.0/24",
            "192.168.2.10 - 192.168.2.20",
            "10.1.1.7   # recepção",
            "copiadora-financeiro",
            "impressora-rh.local",  # já existe
            "300.1.1.1",
            "192.168.3.0/8",
            "",
        ]
    )
    resp = await client.post(
        f"/api/v1/sites/{t.site_id}/ip-ranges/import", json={"content": content}, headers=auth(admin)
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert (out["created"], out["duplicates"]) == (4, 1)
    assert [(e["line"], e["content"]) for e in out["errors"]] == [(7, "300.1.1.1"), (8, "192.168.3.0/8")]
    assert "no máximo /16" in out["errors"][1]["error"]
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    kinds = {(r["cidr"], r["start_ip"], r["end_ip"], r["host"]) for r in cfg["ranges"]}
    assert (None, None, None, "copiadora-financeiro") in kinds
    assert ("192.168.1.0/24", None, None, None) in kinds
    assert (None, "192.168.2.10", "192.168.2.20", None) in kinds
    assert (None, None, None, "10.1.1.7") in kinds


async def test_collector_details_stats_and_local_networks(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    agent = await enrolled_agent(client, t)
    await agent.heartbeat(install_path=r"C:\Program Files\Dati Monitor")
    await agent.send(
        [
            agent.reading("SN-S1", {"total": 1}),
            agent.item("supplies", serial="SN-S1", supplies=[]),
            agent.reading("SN-S2", {"total": 2}, ip="10.0.0.9"),
            agent.item(
                "event", serial="SN-S2", ip="10.0.0.9", event={"type": "read_failed", "data": {"attempts": 3}}
            ),
        ]
    )
    got = (await client.get(f"/api/v1/agents/{agent.agent_id}", headers=auth(admin))).json()
    assert (got["install_path"], got["public_ip"], got["monitor_local_networks"]) == (
        r"C:\Program Files\Dati Monitor",
        "203.0.113.10",  # IP do cliente de teste (ASGITransport)
        False,
    )
    stats = (await client.get(f"/api/v1/agents/{agent.agent_id}/stats", headers=auth(admin))).json()
    assert {
        k: stats[k] for k in ("readings_24h", "items_24h", "failures_24h", "devices_total", "devices_offline")
    } == {
        "readings_24h": 2,
        "items_24h": 4,
        "failures_24h": 1,
        "devices_total": 2,
        "devices_offline": 1,
    }
    before = (await agent.heartbeat())["config_version"]
    resp = await client.patch(
        f"/api/v1/agents/{agent.agent_id}", json={"monitor_local_networks": True}, headers=auth(admin)
    )
    assert resp.json()["monitor_local_networks"] is True
    assert (await agent.heartbeat())["config_version"] == before + 1
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert cfg["monitor_local_networks"] is True

    # Tentativas SNMP de 1 a 5 (retentativas 0 a 4) e timeout de leitura próprio.
    ok = await client.patch(
        f"/api/v1/sites/{t.site_id}",
        json={"collection_config": {"snmp_retries": 4, "snmp_read_timeout_ms": 3000}},
        headers=auth(admin),
    )
    assert ok.status_code == 200, ok.text
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert (cfg["discovery"]["retries"], cfg["discovery"]["read_timeout_ms"]) == (4, 3000)
    too_many = await client.patch(
        f"/api/v1/sites/{t.site_id}", json={"collection_config": {"snmp_retries": 5}}, headers=auth(admin)
    )
    assert too_many.status_code == 422
    # Configuração antiga gravada com 5 retentativas é limitada a 4 na entrega ao coletor.
    async with sessionmaker() as s:
        await s.execute(
            update(Site).where(Site.id == t.site_id).values(collection_config={"snmp_retries": 5})
        )
        await s.commit()
    cfg = (await client.get("/api/agent/config", headers=agent.headers)).json()
    assert cfg["discovery"]["retries"] == 4
