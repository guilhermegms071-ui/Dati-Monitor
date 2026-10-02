"""Conector do Dataclassic (PROMPT 16.11): parameters (secret encrypted and masked), queue with status per
item, what becomes supply request / service order, daily counters, the file / HTTP / e-mail transports,
backoff until `error`, retry from the portal and the queue screens."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import Alert, AuditLog, ErpQueueItem, Setting
from app.services import erp_connector as svc
from tests.alert_helpers import mock_transport
from tests.conftest import Factory, auth, login
from tests.test_reports import SERIAL, build_park

BASE = {
    "enabled": True,
    "company_code": "01",
    "operator": "DM",
    "send_counters": False,
    "counters_hour": 0,
    "supply_request": {"enabled": True, "operation": "5102", "seller": "12", "notify_email": None},
    "service_order": {
        "enabled": True,
        "technician_code": "T9",
        "reason": "Chamado automático",
        "alert_types": ["service_call", "jam_recurrent"],
        "prt_alert_codes": [13],
    },
    "transport": {"kind": "file", "directory": None},
}


def cfg(tmp: Path, **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(json.dumps(BASE))
    data["transport"]["directory"] = str(tmp)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        else:
            data[k] = v
    return data


async def add_alert(
    maker: async_sessionmaker[AsyncSession],
    reseller_id: uuid.UUID,
    device_id: uuid.UUID,
    type_: str,
    *,
    data: dict[str, Any] | None = None,
    opened_at: datetime | None = None,
) -> uuid.UUID:
    async with maker() as s:
        a = Alert(
            reseller_id=reseller_id,
            type=type_,
            severity="warning",
            target_type="device",
            target_id=device_id,
            message=f"alerta {type_} {uuid.uuid4().hex[:4]}",
            dedup_key=f"t:{uuid.uuid4()}",
            data=data or {},
            opened_at=opened_at or datetime.now(UTC),
            notified_at=datetime.now(UTC),
        )
        s.add(a)
        await s.commit()
        return a.id


def written(folder: Path, pattern: str = "*.json") -> list[dict[str, Any]]:
    """JSON files the file transport wrote (sorted by name); fails if a temporary file was left behind."""
    assert not list(folder.glob("*.tmp")), "arquivo temporário esquecido"
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(folder.glob(pattern))]


async def run_worker(
    maker: async_sessionmaker[AsyncSession], settings: Settings, client: httpx.AsyncClient, now: datetime
) -> tuple[int, int, int]:
    async with maker() as s:
        queued = await svc.enqueue(s, settings, now)
        await s.commit()
        sent, failed = await svc.deliver_due(s, settings, client, now)
        await s.commit()
    return queued, sent, failed


async def test_settings_validation_secret_and_permissions(
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    default = (await client.get("/api/v1/erp-connector", headers=auth(admin))).json()
    assert default["enabled"] is False
    assert default["service_order"]["alert_types"] == ["service_call"]

    no_dir = cfg(tmp_path, transport={"kind": "file", "directory": None})
    resp = await client.put("/api/v1/erp-connector", json=no_dir, headers=auth(admin))
    assert resp.json()["detail"]["code"] == "erp_directory_required"
    only_mail = cfg(tmp_path, supply_request={"email_only": True})
    resp = await client.put("/api/v1/erp-connector", json=only_mail, headers=auth(admin))
    assert resp.json()["detail"]["code"] == "erp_email_required"

    http = cfg(
        tmp_path, transport={"kind": "http", "url": "https://erp.test/in", "auth_header": "Bearer s3cr3t"}
    )
    saved = await client.put("/api/v1/erp-connector", json=http, headers=auth(admin))
    assert saved.status_code == 200, saved.text
    assert saved.json()["transport"]["auth_header"] == "••••"
    assert saved.json()["enabled_since"] is not None
    async with sessionmaker() as s:
        row = (await s.execute(select(Setting).where(Setting.key == "erp_connector"))).scalar_one()
        assert "s3cr3t" not in json.dumps(row.value)
        assert row.value_enc is not None
    # Salvar de novo com a máscara mantém o segredo e a data de ativação.
    again = saved.json()
    await client.put("/api/v1/erp-connector", json=again, headers=auth(admin))
    async with sessionmaker() as s:
        loaded = await svc.load(s, test_settings, tenant.reseller_id)
    assert loaded.transport.auth_header == "Bearer s3cr3t"
    assert loaded.enabled_since is not None
    assert loaded.enabled_since.isoformat().startswith(again["enabled_since"][:19])

    _, tech_email = await factory.user(tenant.reseller_id, role="technician")
    tech = await login(client, tech_email)
    assert (await client.get("/api/v1/erp-connector", headers=auth(tech))).status_code == 403
    assert (await client.get("/api/v1/erp-queue", headers=auth(tech))).status_code == 403
    async with sessionmaker() as s:
        logged = (await s.execute(select(AuditLog).where(AuditLog.entity == "erp_connector"))).scalars().all()
    assert logged
    assert "s3cr3t" not in json.dumps([row.after for row in logged])


async def test_alerts_become_requests_and_orders_via_file(
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    park = await build_park(client, factory)
    rid = park.tenant.reseller_id
    old_alert_device = await _device_id(sessionmaker, SERIAL)
    await add_alert(
        sessionmaker, rid, old_alert_device, "toner_low", opened_at=datetime.now(UTC) - timedelta(days=1)
    )
    assert (
        await client.put("/api/v1/erp-connector", json=cfg(tmp_path), headers=auth(park.admin))
    ).status_code == 200
    device = old_alert_device
    toner = await add_alert(sessionmaker, rid, device, "toner_low")
    call = await add_alert(
        sessionmaker, rid, device, "printer_alert", data={"category": "service_call", "code": 13}
    )
    await add_alert(sessionmaker, rid, device, "printer_alert", data={"category": "service_call", "code": 99})
    await add_alert(sessionmaker, rid, device, "printer_alert", data={"category": "other", "code": 13})
    jam = await add_alert(sessionmaker, rid, device, "jam_recurrent", data={"count": 5, "days": 3})

    async with httpx.AsyncClient() as http:
        queued, sent, failed = await run_worker(sessionmaker, test_settings, http, datetime.now(UTC))
        assert (queued, sent, failed) == (3, 3, 0)
        assert (await run_worker(sessionmaker, test_settings, http, datetime.now(UTC)))[0] == 0, (
            "sem duplicar"
        )

    async with sessionmaker() as s:
        items = {i.alert_id: i for i in (await s.execute(select(ErpQueueItem))).scalars()}
    assert set(items) == {toner, call, jam}, "o alerta anterior à ativação e os filtrados ficam de fora"
    assert items[toner].kind == "supply_request"
    assert {items[call].kind, items[jam].kind} == {"service_order"}
    assert all(i.status == "sent" and i.delivered_via == "file" for i in items.values())

    docs = written(tmp_path)
    assert len(docs) == 3
    order = next(d for d in docs if d["kind"] == "service_order" and d["alert"]["type"] == "printer_alert")
    assert order["company_code"] == "01"
    assert order["order"]["technician_code"] == "T9"
    assert order["order"]["category"] == "service_call"
    assert order["device"]["serial"] == SERIAL
    assert order["customer"]["erp_code"] == "E-Revenda A"
    request = next(d for d in docs if d["kind"] == "supply_request")
    assert request["request"]["operation"] == "5102"

    listed = (await client.get("/api/v1/erp-queue", headers=auth(park.admin))).json()
    assert len(listed["items"]) == 3
    counts = (await client.get("/api/v1/erp-queue/counts", headers=auth(park.admin))).json()
    assert counts == {"pending": 0, "sent": 3, "error": 0}
    detail = (
        await client.get(f"/api/v1/erp-queue/{listed['items'][0]['id']}", headers=auth(park.admin))
    ).json()
    assert detail["payload"]["layout"] == "dati-monitor/erp/1"


async def test_http_backoff_error_and_retry(
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    park = await build_park(client, factory)
    http_cfg = cfg(
        tmp_path,
        transport={"kind": "http", "url": "https://erp.test/in", "auth_header": "Bearer s3cr3t"},
        service_order={"enabled": False},
    )
    assert (
        await client.put("/api/v1/erp-connector", json=http_cfg, headers=auth(park.admin))
    ).status_code == 200
    await add_alert(
        sessionmaker, park.tenant.reseller_id, await _device_id(sessionmaker, SERIAL), "toner_low"
    )

    seen: list[httpx.Request] = []
    now = datetime.now(UTC)
    async with httpx.AsyncClient(transport=mock_transport([500] * svc.MAX_ATTEMPTS + [201], seen)) as http:
        assert await run_worker(sessionmaker, test_settings, http, now) == (1, 0, 1)
        async with sessionmaker() as s:
            item = (await s.execute(select(ErpQueueItem))).scalar_one()
        assert (item.status, item.attempts) == ("pending", 1)
        assert item.last_error is not None
        assert "HTTP 500" in item.last_error
        assert item.next_attempt_at == now + timedelta(minutes=1)
        # Sem esperar o backoff, nada sai.
        assert await run_worker(sessionmaker, test_settings, http, now) == (0, 0, 0)
        for minutes in (2, 5, 15, 30, 60):
            now += timedelta(minutes=minutes + 1)
            await run_worker(sessionmaker, test_settings, http, now)
        async with sessionmaker() as s:
            item = (await s.execute(select(ErpQueueItem))).scalar_one()
        assert (item.status, item.attempts) == ("error", svc.MAX_ATTEMPTS)

        resp = await client.post(
            "/api/v1/erp-queue/retry", json={"ids": [str(item.id)]}, headers=auth(park.admin)
        )
        assert resp.json() == {"requeued": 1}
        assert await run_worker(
            sessionmaker, test_settings, http, datetime.now(UTC) + timedelta(seconds=5)
        ) == (0, 1, 0)
    last = seen[-1]
    assert last.headers["Authorization"] == "Bearer s3cr3t"
    assert last.headers["Idempotency-Key"] == str(item.id)
    assert json.loads(last.content)["kind"] == "supply_request"
    async with sessionmaker() as s:
        item = (await s.execute(select(ErpQueueItem))).scalar_one()
        assert (item.status, item.delivered_via, item.attempts) == ("sent", "http", 1)
        actions = set(
            (await s.execute(select(AuditLog.action).where(AuditLog.entity == "erp_queue"))).scalars()
        )
    assert actions == {"retry"}


async def test_email_only_and_daily_counters(
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    mail_catcher: Any,
    tmp_path: Path,
) -> None:
    park = await build_park(client, factory)
    body = cfg(
        tmp_path,
        send_counters=True,
        supply_request={"email_only": True, "notify_email": "Compras@Daticopy.test"},
        service_order={"enabled": False},
    )
    assert (await client.put("/api/v1/erp-connector", json=body, headers=auth(park.admin))).status_code == 200
    await add_alert(
        sessionmaker, park.tenant.reseller_id, await _device_id(sessionmaker, SERIAL), "toner_low"
    )
    async with httpx.AsyncClient() as http:
        assert await run_worker(sessionmaker, test_settings, http, datetime.now(UTC)) == (2, 2, 0)
    mail = next(m for m in mail_catcher.store.list() if m["to"] == ["compras@daticopy.test"])
    assert mail["subject"].startswith("Requisição de suprimento")
    assert f"série {SERIAL}" in mail["text"]

    (counters,) = written(tmp_path, "counters-*.json")
    row = next(r for r in counters["readings"] if r["serial"] == SERIAL)
    assert (row["total"], row["mono"], row["color"]) == (1600, 1150, 450), "leitura de corte, sem a regressão"
    assert row["customer_erp_code"] == "E-Revenda A"
    async with sessionmaker() as s:
        kinds = {i.kind: i.delivered_via for i in (await s.execute(select(ErpQueueItem))).scalars()}
    assert kinds == {"supply_request": "email", "counters": "file"}


async def _device_id(maker: async_sessionmaker[AsyncSession], serial: str) -> uuid.UUID:
    from app.models import Device  # noqa: PLC0415

    async with maker() as s:
        return (await s.execute(select(Device.id).where(Device.serial == serial))).scalar_one()
