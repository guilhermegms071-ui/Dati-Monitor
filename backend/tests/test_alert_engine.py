"""Alert rules and evaluation (PROMPT 8, 9 and 16): defaults, customer overrides, collector offline with
grouping, toner thresholds and forecast, printer errors, recurrent jams and automatic resolution."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Alert, Device, PrinterAlert, SupplyCurrent
from tests.agent_helpers import enrolled_agent
from tests.alert_helpers import device_via_agent, evaluate, open_alerts, set_agent, set_device, supply
from tests.conftest import Factory, auth, login


async def test_default_rules_and_customer_override(client: httpx.AsyncClient, factory: Factory) -> None:
    t = await factory.tenant()
    admin = await login(client, t.admin_email)
    rules = (await client.get("/api/v1/alert-rules", headers=auth(admin))).json()
    by_type = {r["type"]: r for r in rules}
    assert set(by_type) == {
        "agent_offline",
        "device_no_reading",
        "toner_low",
        "toner_days_left",
        "hardware_error",
        "paper_jam",
        "door_open",
        "jam_recurrent",
        "printer_alert",
        "counter_regression",
        "suspicious_jump",
        "sum_mismatch",
        "agent_uninstalled",
        "device_transfer",
    }
    assert by_type["agent_offline"]["params"] == {"minutes": 5}
    assert by_type["jam_recurrent"]["params"] == {"count": 5, "days": 3}
    assert by_type["agent_offline"]["type_label"] == "Coletor sem sinal"

    rid = by_type["toner_days_left"]["id"]
    ok = await client.patch(f"/api/v1/alert-rules/{rid}", json={"params": {"days": 10}}, headers=auth(admin))
    assert ok.json()["params"] == {"days": 10, "min_confidence": 0.5}
    bad = await client.patch(f"/api/v1/alert-rules/{rid}", json={"params": {"days": 0}}, headers=auth(admin))
    assert bad.status_code == 400
    assert "days" in bad.json()["detail"]["message"]

    body = {
        "customer_id": str(t.customer_id),
        "name": "Cliente tolerante",
        "type": "agent_offline",
        "params": {"minutes": 60},
        "severity": "warning",
    }
    created = await client.post("/api/v1/alert-rules", json=body, headers=auth(admin))
    assert created.status_code == 201, created.text
    assert created.json()["customer_name"] == "Revenda A Cliente"
    assert (await client.post("/api/v1/alert-rules", json=body, headers=auth(admin))).status_code == 400
    reseller_wide = {**body, "customer_id": None}
    assert (
        await client.post("/api/v1/alert-rules", json=reseller_wide, headers=auth(admin))
    ).status_code == 400
    delete_default = await client.delete(
        f"/api/v1/alert-rules/{by_type['paper_jam']['id']}", headers=auth(admin)
    )
    assert delete_default.status_code == 400
    assert (
        await client.delete(f"/api/v1/alert-rules/{created.json()['id']}", headers=auth(admin))
    ).status_code == 204

    # Operador lê as regras, mas não as altera (alert_rules.write é do administrador).
    _, op_email = await factory.user(t.reseller_id, role="operator")
    op = await login(client, op_email)
    assert (await client.get("/api/v1/alert-rules", headers=auth(op))).status_code == 200
    denied = await client.patch(f"/api/v1/alert-rules/{rid}", json={"enabled": False}, headers=auth(op))
    assert denied.status_code == 403
    # Outra revenda tem as próprias regras.
    other = await factory.tenant("Revenda B")
    other_rules = (
        await client.get("/api/v1/alert-rules", headers=auth(await login(client, other.admin_email)))
    ).json()
    assert {r["id"] for r in other_rules}.isdisjoint({r["id"] for r in rules})


async def test_collector_offline_groups_devices_and_resolves(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    a = await enrolled_agent(client, t, name="PC A")
    await a.heartbeat()
    await device_via_agent(a, "SN-1")
    await device_via_agent(a, "SN-2", ip="10.0.0.6")
    now = datetime.now(UTC)
    await set_agent(sessionmaker, a.agent_id, state="offline", last_seen_at=now - timedelta(minutes=10))
    for serial in ("SN-1", "SN-2"):
        await set_device(sessionmaker, serial, last_read_at=now - timedelta(hours=10))

    # O local inteiro ficou sem coletor: um alerta do coletor, nenhum por equipamento (seção 9).
    r = await evaluate(sessionmaker, now)
    assert r.opened == 1
    assert set(await open_alerts(sessionmaker)) == {f"agent_offline:{a.agent_id}"}
    async with sessionmaker() as s:
        alert = (await s.execute(select(Alert))).scalar_one()
        assert (alert.severity, alert.target_type, alert.customer_id) == ("critical", "agent", t.customer_id)
        assert alert.message.startswith("PC A sem sinal desde ")
    assert (await evaluate(sessionmaker, now)).opened == 0  # deduplicado

    # Outro coletor do local está vivo: os equipamentos sem leitura passam a alertar.
    b = await enrolled_agent(client, t, name="PC B")
    await b.heartbeat()
    await b.watchdog_heartbeat()  # a desinstalação pelo portal é feita pelo vigia
    await evaluate(sessionmaker, now)
    keys = set(await open_alerts(sessionmaker))
    assert {k.split(":")[0] for k in keys} == {"agent_offline", "device_no_reading"}
    assert len([k for k in keys if k.startswith("device_no_reading")]) == 2

    # O coletor volta: o alerta se resolve sozinho.
    await set_agent(sessionmaker, a.agent_id, state="online", last_seen_at=now)
    r = await evaluate(sessionmaker, now)
    assert r.resolved == 1
    async with sessionmaker() as s:
        resolved = (await s.execute(select(Alert).where(Alert.type == "agent_offline"))).scalar_one()
        assert resolved.state == "resolved"
        assert resolved.data["auto_resolved"] is True

    # Pausado (manutenção no cliente) não alerta; regra do cliente com 60 min espera mais.
    await set_agent(
        sessionmaker, a.agent_id, state="offline", last_seen_at=now - timedelta(minutes=10), paused=True
    )
    await evaluate(sessionmaker, now)
    assert f"agent_offline:{a.agent_id}" not in await open_alerts(sessionmaker)
    await set_agent(sessionmaker, a.agent_id, paused=False)
    admin = await login(client, t.admin_email)
    await client.post(
        "/api/v1/alert-rules",
        json={
            "customer_id": str(t.customer_id),
            "name": "60 min",
            "type": "agent_offline",
            "params": {"minutes": 60},
        },
        headers=auth(admin),
    )
    await evaluate(sessionmaker, now)
    assert f"agent_offline:{a.agent_id}" not in await open_alerts(sessionmaker)
    await evaluate(sessionmaker, now + timedelta(hours=1))
    assert f"agent_offline:{a.agent_id}" in await open_alerts(sessionmaker)


async def test_toner_thresholds_forecast_and_monitoring_switches(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    a = await enrolled_agent(client, t)
    await a.heartbeat()
    await device_via_agent(
        a,
        "SN-T",
        supplies=[supply("1.1", "black", 8), supply("1.2", "cyan", 15), supply("1.3", "magenta", 50)],
    )
    admin = await login(client, t.admin_email)

    async def toner_keys() -> set[str]:
        await evaluate(sessionmaker)
        return {k.rsplit(":", 1)[1] for k in await open_alerts(sessionmaker) if k.startswith("toner_low")}

    assert await toner_keys() == {"1.1"}  # padrão 10%
    await client.patch(
        f"/api/v1/customers/{t.customer_id}",
        json={"toner_thresholds": {"black": 10, "cyan": 20, "magenta": 10, "yellow": 10}},
        headers=auth(admin),
    )
    assert await toner_keys() == {"1.1", "1.2"}
    device_id = await set_device(sessionmaker, "SN-T")
    await client.patch(
        f"/api/v1/devices/{device_id}",
        json={
            "toner_mode": "individual",
            "toner_thresholds": {"black": 5, "cyan": 20, "magenta": 10, "yellow": 10},
        },
        headers=auth(admin),
    )
    assert await toner_keys() == {"1.2"}  # preto em 8% passa, pois o limiar próprio é 5%
    await client.patch(f"/api/v1/devices/{device_id}", json={"toner_mode": "off"}, headers=auth(admin))
    assert await toner_keys() == set()
    await client.patch(f"/api/v1/devices/{device_id}", json={"toner_mode": "global"}, headers=auth(admin))
    await client.patch(
        f"/api/v1/customers/{t.customer_id}", json={"toner_monitoring": False}, headers=auth(admin)
    )
    assert await toner_keys() == set()
    await client.patch(
        f"/api/v1/customers/{t.customer_id}", json={"toner_monitoring": True}, headers=auth(admin)
    )

    # Previsão: só alerta com confiança suficiente (previsão incerta não vira alerta).
    async with sessionmaker() as s:
        await s.execute(
            update(SupplyCurrent)
            .where(SupplyCurrent.device_id == device_id, SupplyCurrent.supply_key == "1.3")
            .values(
                days_to_empty=Decimal("3.0"),
                days_to_empty_min=Decimal("2.0"),
                days_to_empty_max=Decimal("5.0"),
                forecast_confidence=Decimal("0.300"),
            )
        )
        await s.commit()
    await evaluate(sessionmaker)
    assert not any(k.startswith("toner_days_left") for k in await open_alerts(sessionmaker))
    async with sessionmaker() as s:
        await s.execute(
            update(SupplyCurrent)
            .where(SupplyCurrent.device_id == device_id, SupplyCurrent.supply_key == "1.3")
            .values(forecast_confidence=Decimal("0.800"))
        )
        await s.commit()
    await evaluate(sessionmaker)
    async with sessionmaker() as s:
        alert = (await s.execute(select(Alert).where(Alert.type == "toner_days_left"))).scalar_one()
    assert alert.severity == "info"
    assert alert.data["days_min"] == 2.0
    assert alert.data["days_max"] == 5.0
    assert "acaba em cerca de 3 dia(s)" in alert.message


async def test_printer_errors_recurrent_jam_and_disabled_rule(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    a = await enrolled_agent(client, t)
    await a.heartbeat()
    await device_via_agent(a, "SN-E")
    device_id = await set_device(
        sessionmaker,
        "SN-E",
        last_status="error",
        last_error_reasons=["jammed", "doorOpen", "serviceRequested"],
    )
    await evaluate(sessionmaker)
    kinds = {k.split(":")[0] for k in await open_alerts(sessionmaker)}
    assert kinds == {"paper_jam", "door_open", "hardware_error"}

    # Equipamento sem resposta: o status antigo não é confiável e os alertas de erro se resolvem.
    await set_device(sessionmaker, "SN-E", last_status="offline")
    await evaluate(sessionmaker)
    assert await open_alerts(sessionmaker) == {}

    now = datetime.now(UTC)
    async with sessionmaker() as s:
        device = await s.get(Device, device_id)
        assert device is not None
        for i in range(5):
            s.add(
                PrinterAlert(
                    reseller_id=device.reseller_id,
                    device_id=device.id,
                    alert_key=f"jam-{i}",
                    severity=3,
                    code=8,
                    category="jam",
                    first_seen_at=now - timedelta(hours=10 * i),
                    last_seen_at=now,
                    cleared_at=now,
                )
            )
        s.add(
            PrinterAlert(
                reseller_id=device.reseller_id,
                device_id=device.id,
                alert_key="svc",
                severity=3,
                code=30,
                category="service_call",
                description="Falha no fusor",
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        await s.commit()
    await evaluate(sessionmaker, now)
    alerts = await open_alerts(sessionmaker)
    assert f"jam_recurrent:{device_id}" in alerts
    assert any(k.startswith("printer_alert:") for k in alerts)
    # 5 em 3 dias: com a janela de 1 dia só 3 atolamentos contam, e o alerta se resolve.
    admin = await login(client, t.admin_email)
    rules = {
        r["type"]: r["id"] for r in (await client.get("/api/v1/alert-rules", headers=auth(admin))).json()
    }
    await client.patch(
        f"/api/v1/alert-rules/{rules['jam_recurrent']}", json={"params": {"days": 1}}, headers=auth(admin)
    )
    await evaluate(sessionmaker, now)
    assert f"jam_recurrent:{device_id}" not in await open_alerts(sessionmaker)
    # Regra desativada: o alerta se resolve e não volta.
    await client.patch(
        f"/api/v1/alert-rules/{rules['printer_alert']}", json={"enabled": False}, headers=auth(admin)
    )
    await evaluate(sessionmaker, now)
    assert not any(k.startswith("printer_alert:") for k in await open_alerts(sessionmaker))


async def test_uninstall_notice_alerts_once_and_skips_offline(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """O desinstalador avisa o servidor: alerta "Coletor desinstalado" (não o "sem sinal"); se a
    desinstalação foi pedida pelo portal, não alerta; se o coletor volta a falar, a marca some."""
    t = await factory.tenant()
    a = await enrolled_agent(client, t, name="PC Recepção")
    await a.heartbeat()
    resp = await client.post(
        "/api/agent/uninstalling", json={"v": 1, "reason": "installer"}, headers=a.headers
    )
    assert resp.status_code == 200, resp.text
    async with sessionmaker() as s:
        alert = (await s.execute(select(Alert).where(Alert.type == "agent_uninstalled"))).scalar_one()
        assert (alert.severity, alert.customer_id) == ("critical", t.customer_id)
        assert alert.message == "Coletor PC Recepção foi desinstalado do PC (PC-TESTE)"
    admin = await login(client, t.admin_email)
    agent = (await client.get(f"/api/v1/agents/{a.agent_id}", headers=auth(admin))).json()
    assert agent["uninstalled_at"] is not None
    assert agent["state"] == "offline"

    # Sem sinal depois de desinstalado: não abre o "Coletor sem sinal" em cima do alerta próprio.
    now = datetime.now(UTC)
    await set_agent(sessionmaker, a.agent_id, last_seen_at=now - timedelta(hours=1))
    await evaluate(sessionmaker, now)
    assert f"agent_offline:{a.agent_id}" not in await open_alerts(sessionmaker)

    # Voltou a falar (reinstalado): a marca some.
    await a.heartbeat()
    agent = (await client.get(f"/api/v1/agents/{a.agent_id}", headers=auth(admin))).json()
    assert agent["uninstalled_at"] is None

    # Desinstalação pedida pelo portal: não alerta.
    b = await enrolled_agent(client, t, name="PC B")
    await b.heartbeat()
    await b.watchdog_heartbeat()  # a desinstalação pelo portal é feita pelo vigia
    cmd = await client.post(
        f"/api/v1/agents/{b.agent_id}/commands",
        json={"type": "uninstall", "params": {"confirm_name": "PC B"}},
        headers=auth(admin),
    )
    assert cmd.status_code in (200, 201), cmd.text
    resp = await client.post("/api/agent/uninstalling", json={"v": 1, "reason": "package"}, headers=b.headers)
    assert resp.status_code == 200
    assert f"agent_uninstalled:{b.agent_id}" not in await open_alerts(sessionmaker)
