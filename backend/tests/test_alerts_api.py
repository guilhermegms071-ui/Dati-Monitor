"""Alertas no portal: list, counts, acknowledge/resolve, isolation between resellers and customers, printer
alerts (16.4) and supply replacements (16.3) screens."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import PrinterAlert
from app.services.alerts import open_alert
from tests.agent_helpers import enrolled_agent
from tests.alert_helpers import device_via_agent, set_device, supply
from tests.conftest import Factory, Tenant, auth, login


async def _open(
    maker: async_sessionmaker[AsyncSession], t: Tenant, key: str, severity: str, **kw: object
) -> None:
    async with maker() as s:
        await open_alert(
            s,
            reseller_id=t.reseller_id,
            type_=str(kw.get("type_", "toner_low")),
            severity=severity,
            target_type="device",
            target_id=uuid.uuid4(),
            message=f"Alerta {key}",
            dedup_key=key,
            customer_id=kw.get("customer_id", t.customer_id),  # type: ignore[arg-type]
        )
        await s.commit()


async def test_list_counts_act_and_isolation(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    other = await factory.tenant("Revenda B")
    for i, sev in enumerate(("critical", "warning", "warning", "info")):
        await _open(sessionmaker, t, f"a-{i}", sev)
    await _open(sessionmaker, other, "b-0", "critical")
    admin = await login(client, t.admin_email)

    page = (await client.get("/api/v1/alerts", headers=auth(admin))).json()
    assert page["total"] == 4
    assert {a["message"] for a in page["items"]} == {"Alerta a-0", "Alerta a-1", "Alerta a-2", "Alerta a-3"}
    assert page["items"][0]["type_label"] == "Toner abaixo do limiar"
    assert page["items"][0]["customer_name"] == "Revenda A Cliente"
    counts = (await client.get("/api/v1/alerts/counts", headers=auth(admin))).json()
    assert counts == {"open": 4, "critical": 1, "warning": 2, "info": 1}
    crit = (await client.get("/api/v1/alerts", params={"severity": "critical"}, headers=auth(admin))).json()
    ids = [a["id"] for a in crit["items"]]
    assert len(ids) == 1

    ack = await client.post(
        "/api/v1/alerts/act", json={"alert_ids": ids, "action": "acknowledge"}, headers=auth(admin)
    )
    assert ack.json() == {"changed": 1}
    detail = (await client.get(f"/api/v1/alerts/{ids[0]}", headers=auth(admin))).json()
    assert detail["state"] == "acknowledged"
    assert detail["acknowledged_by"] is not None
    assert (await client.get("/api/v1/alerts/counts", headers=auth(admin))).json()[
        "open"
    ] == 4  # ainda não resolvido
    everything = [a["id"] for a in page["items"]]
    res = await client.post(
        "/api/v1/alerts/act", json={"alert_ids": everything, "action": "resolve"}, headers=auth(admin)
    )
    assert res.json() == {"changed": 4}
    assert (await client.get("/api/v1/alerts/counts", headers=auth(admin))).json()["open"] == 0
    resolved = (await client.get("/api/v1/alerts", params={"state": "resolved"}, headers=auth(admin))).json()
    assert resolved["total"] == 4

    # Outra revenda: não vê nem mexe nos alertas desta.
    b_admin = await login(client, other.admin_email)
    assert (await client.get("/api/v1/alerts", headers=auth(b_admin))).json()["total"] == 1
    assert (await client.get(f"/api/v1/alerts/{ids[0]}", headers=auth(b_admin))).status_code == 404
    nope = await client.post(
        "/api/v1/alerts/act", json={"alert_ids": ids, "action": "resolve"}, headers=auth(b_admin)
    )
    assert nope.json() == {"changed": 0}

    # Usuário de cliente: só os alertas do próprio cliente; não reconhece nem resolve.
    second = await factory.customer(t.reseller_id, t.company_id, "Outro cliente")
    await _open(sessionmaker, t, "c-mine", "warning")
    await _open(sessionmaker, t, "c-other", "warning", customer_id=second)
    _, viewer_email = await factory.user(t.reseller_id, role="customer_viewer", customer_id=t.customer_id)
    viewer = await login(client, viewer_email)
    seen = (await client.get("/api/v1/alerts", headers=auth(viewer))).json()
    assert [a["message"] for a in seen["items"]] == ["Alerta c-mine"]
    forbidden = await client.post(
        "/api/v1/alerts/act",
        json={"alert_ids": [seen["items"][0]["id"]], "action": "resolve"},
        headers=auth(viewer),
    )
    assert forbidden.status_code == 403


async def test_printer_alerts_and_replacements_screens(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    t = await factory.tenant()
    agent = await enrolled_agent(client, t)
    await device_via_agent(agent, "SN-P", supplies=[supply("1.1", "black", 5)])
    device_id = await set_device(sessionmaker, "SN-P")
    # Troca de toner pela ingestão real (nível sobe de 5% para 100%).
    items = [
        agent.item(
            "supplies",
            serial="SN-P",
            read_at=datetime.now(UTC) + timedelta(minutes=1),
            supplies=[supply("1.1", "black", 100)],
        ),
    ]
    assert (await agent.send(items))[0]["status"] == "accepted"
    now = datetime.now(UTC)
    async with sessionmaker() as s:
        for i, (cat, cleared) in enumerate((("jam", now), ("service_call", None), ("consumable", None))):
            s.add(
                PrinterAlert(
                    reseller_id=t.reseller_id,
                    device_id=device_id,
                    alert_key=f"k{i}",
                    severity=3,
                    code=8 + i,
                    category=cat,
                    description=f"Descrição {cat}",
                    first_seen_at=now - timedelta(minutes=i),
                    last_seen_at=now,
                    cleared_at=cleared,
                )
            )
        await s.commit()
    admin = await login(client, t.admin_email)

    all_alerts = (await client.get("/api/v1/printer-alerts", headers=auth(admin))).json()
    assert [a["category"] for a in all_alerts["items"]] == [
        "jam",
        "service_call",
        "consumable",
    ]  # mais recentes primeiro
    assert all_alerts["items"][0]["serial"] == "SN-P"
    active = (await client.get("/api/v1/printer-alerts", params={"active": True}, headers=auth(admin))).json()
    assert active["total"] == 2
    counts = (await client.get("/api/v1/printer-alerts/counts", headers=auth(admin))).json()
    assert counts == {"parts": 0, "service_call": 1, "jam": 0, "consumable": 1, "other": 0}

    reps = (await client.get("/api/v1/supply-replacements", headers=auth(admin))).json()
    assert reps["total"] == 1
    rep = reps["items"][0]
    assert (rep["serial"], rep["color"], rep["level_before"], rep["level_after"], rep["premature"]) == (
        "SN-P",
        "black",
        "5.00",
        "100.00",
        False,
    )
    assert (
        await client.get("/api/v1/supply-replacements", params={"premature": True}, headers=auth(admin))
    ).json()["total"] == 0
    other = await factory.tenant("Revenda B")
    b = await login(client, other.admin_email)
    assert (await client.get("/api/v1/supply-replacements", headers=auth(b))).json()["total"] == 0
    assert (await client.get("/api/v1/printer-alerts", headers=auth(b))).json()["total"] == 0
