"""Conector do Dataclassic (PROMPT 16.11): company-wide parameters, the `erp_queue` (status per item,
attempts, last error, retry from the portal) and pluggable transports.

What goes into the queue:
- counters: once a day (after `counters_hour`, Brasília), the cutoff reading of every active device at the
  end of the previous day (same rules as the reports and the ERP API);
- supply_request: every `toner_low` alert opened while the connector is on;
- service_order: every alert of the chosen types (Chamado técnico, Consumível, Peças/manutenção, Outros from
  the printer's prtAlertTable, and Atolamento recorrente), optionally limited to a list of prtAlert codes.

Transports: `file` (one JSON per item in a folder the ERP imports) and `http` (POST JSON). The payload is
our own documented JSON (docs/erp-dataclassic.md) until Databit sends the Dataclassic layout; the database
transport waits for that layout too. "Apenas enviar e-mail" sends the supply request by e-mail instead.
"""

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import Settings
from app.core.errors import bad_request, not_found
from app.core.principal import Principal
from app.models import Alert, Customer, Device, ErpQueueItem, Setting, Site
from app.schemas.erp_connector import (
    ErpConnectorSettings,
    ErpQueueCounts,
    ErpQueueDetail,
    ErpQueueItemOut,
    ErpQueuePage,
)
from app.services import audit
from app.services.notifiers import Message, NotifyError, SmtpNotifier
from app.services.pagination import SortOption, paginate
from app.services.reports.base import DISPLAY_TZ
from app.services.reports.counters import cutoff

logger = logging.getLogger(__name__)

SETTINGS_KEY = "erp_connector"
MASK = "••••"
BACKOFF_MINUTES = (1, 2, 5, 15, 30, 60)
MAX_ATTEMPTS = 6
BATCH = 50
PRINTER_ALERT_TYPES = {"service_call", "consumable", "parts", "other"}
KIND_LABELS = {"counters": "Contadores", "supply_request": "Requisição de suprimento", "service_order": "OS"}


class TransportError(Exception):
    """Falha de envio: vai para `last_error` do item e o item volta para a fila com espera."""


# ----------------------------------------------------------------------------- parâmetros


def _aad(reseller_id: uuid.UUID) -> bytes:
    return b"erp_connector:" + reseller_id.bytes


async def load(session: AsyncSession, settings: Settings, reseller_id: uuid.UUID) -> ErpConnectorSettings:
    """Parameters with the secret (Authorization header) decrypted, for the worker."""
    row = (
        await session.execute(
            select(Setting).where(Setting.reseller_id == reseller_id, Setting.key == SETTINGS_KEY)
        )
    ).scalar_one_or_none()
    data = ErpConnectorSettings.model_validate((row.value if row else None) or {})
    if row is not None and row.value_enc:
        secret = crypto.decrypt_json(
            settings.master_key_bytes, row.value_enc, associated_data=_aad(reseller_id)
        )
        data.transport.auth_header = secret.get("auth_header")
    return data


def masked(data: ErpConnectorSettings) -> ErpConnectorSettings:
    out = data.model_copy(deep=True)
    if out.transport.auth_header:
        out.transport.auth_header = MASK
    return out


async def get_settings(session: AsyncSession, settings: Settings, p: Principal) -> ErpConnectorSettings:
    p.require("integration.read")
    return masked(await load(session, settings, p.reseller_id))


async def put_settings(
    session: AsyncSession, settings: Settings, p: Principal, data: ErpConnectorSettings
) -> ErpConnectorSettings:
    p.require("integration.update")
    before = await load(session, settings, p.reseller_id)
    if data.transport.auth_header == MASK:  # campo não alterado na tela
        data.transport.auth_header = before.transport.auth_header
    if data.enabled and data.transport.kind == "file" and not data.transport.directory:
        raise bad_request("erp_directory_required", "Informe a pasta do transporte por arquivo")
    if data.enabled and data.transport.kind == "http" and not data.transport.url:
        raise bad_request("erp_url_required", "Informe a URL do transporte HTTP")
    if data.supply_request.email_only and not data.supply_request.notify_email:
        raise bad_request("erp_email_required", "'Apenas enviar e-mail' precisa do e-mail de notificação")
    # Ligar agora: só alertas abertos daqui em diante viram requisição/OS (nada de mandar o histórico).
    data.enabled_since = (before.enabled_since if before.enabled else None) or (
        datetime.now(UTC) if data.enabled else None
    )
    secret = data.transport.auth_header
    public = data.model_copy(deep=True)
    public.transport.auth_header = None
    value = public.model_dump(mode="json")
    enc = (
        crypto.encrypt_json(
            settings.master_key_bytes, {"auth_header": secret}, associated_data=_aad(p.reseller_id)
        )
        if secret
        else None
    )
    await session.execute(
        pg_insert(Setting)
        .values(reseller_id=p.reseller_id, key=SETTINGS_KEY, value=value, value_enc=enc)
        .on_conflict_do_update(
            index_elements=[Setting.reseller_id, Setting.key], set_={"value": value, "value_enc": enc}
        )
    )
    await audit.record(
        session, p, action="update", entity="erp_connector", entity_id=None, reseller_id=p.reseller_id,
        before=masked(before).model_dump(mode="json"), after=masked(data).model_dump(mode="json"),
    )  # fmt: skip
    return masked(data)


# ----------------------------------------------------------------------------- fila (portal)

SORTS = {"created_at": SortOption(ErpQueueItem.created_at, "datetime")}


async def list_queue(
    session: AsyncSession,
    p: Principal,
    *,
    status: str | None,
    kind: str | None,
    cursor: str | None,
    limit: int,
) -> ErpQueuePage:
    p.require("integration.read")
    stmt = select(ErpQueueItem).where(ErpQueueItem.reseller_id == p.reseller_id)
    if status:
        stmt = stmt.where(ErpQueueItem.status == status)
    if kind:
        stmt = stmt.where(ErpQueueItem.kind == kind)
    page = await paginate(
        session, stmt, id_column=ErpQueueItem.id, sort_options=SORTS, sort="created_at", direction="desc",
        limit=limit, cursor=cursor,
    )  # fmt: skip
    return ErpQueuePage(
        items=[ErpQueueItemOut.model_validate(i) for i in page.items], next_cursor=page.next_cursor
    )


async def counts(session: AsyncSession, p: Principal) -> ErpQueueCounts:
    p.require("integration.read")
    rows = dict(
        (
            await session.execute(
                select(ErpQueueItem.status, func.count())
                .where(ErpQueueItem.reseller_id == p.reseller_id)
                .group_by(ErpQueueItem.status)
            )
        )
        .tuples()
        .all()
    )
    return ErpQueueCounts(
        pending=rows.get("pending", 0), sent=rows.get("sent", 0), error=rows.get("error", 0)
    )


async def get_item(session: AsyncSession, p: Principal, item_id: uuid.UUID) -> ErpQueueDetail:
    p.require("integration.read")
    row = await session.get(ErpQueueItem, item_id)
    if row is None or row.reseller_id != p.reseller_id:
        raise not_found("Item da fila")
    return ErpQueueDetail.model_validate(row)


async def retry(session: AsyncSession, p: Principal, ids: Sequence[uuid.UUID]) -> int:
    """Reenvio pelo portal: volta para pendente, zera as tentativas e envia no próximo ciclo."""
    p.require("integration.update")
    result = await session.execute(
        update(ErpQueueItem)
        .where(
            ErpQueueItem.id.in_(ids),
            ErpQueueItem.reseller_id == p.reseller_id,
            ErpQueueItem.status.in_(("error", "sent")),
        )
        .values(status="pending", attempts=0, next_attempt_at=func.now(), last_error=None)
        .returning(ErpQueueItem.id)
    )
    done = [str(i) for i in result.scalars()]
    if done:
        await audit.record(
            session, p, action="retry", entity="erp_queue", entity_id=None, reseller_id=p.reseller_id,
            after={"ids": done},
        )  # fmt: skip
    return len(done)


# ----------------------------------------------------------------------------- enfileirar (worker)


def _header(cfg: ErpConnectorSettings, kind: str) -> dict[str, Any]:
    return {
        "layout": "dati-monitor/erp/1",
        "kind": kind,
        "company_code": cfg.company_code,
        "operator": cfg.operator,
    }


async def _add(session: AsyncSession, reseller_id: uuid.UUID, now: datetime, **values: Any) -> bool:
    result = await session.execute(
        pg_insert(ErpQueueItem)
        .values(reseller_id=reseller_id, next_attempt_at=now, **values)
        .on_conflict_do_nothing(index_elements=[ErpQueueItem.reseller_id, ErpQueueItem.dedup_key])
        .returning(ErpQueueItem.id)
    )
    return result.scalar_one_or_none() is not None


def _os_category(alert: Alert) -> str | None:
    if alert.type == "jam_recurrent":
        return "jam_recurrent"
    if alert.type == "printer_alert":
        category = alert.data.get("category")
        return category if category in PRINTER_ALERT_TYPES else None
    return None


async def _alert_items(
    session: AsyncSession, reseller_id: uuid.UUID, cfg: ErpConnectorSettings, since: datetime, now: datetime
) -> int:
    types: list[str] = []
    if cfg.supply_request.enabled:
        types.append("toner_low")
    if cfg.service_order.enabled:
        types += ["printer_alert", "jam_recurrent"]
    if not types:
        return 0
    rows = (
        await session.execute(
            select(Alert, Device, Customer, Site.name)
            .join(Device, and_(Alert.target_type == "device", Device.id == Alert.target_id))
            .join(Customer, Customer.id == Device.customer_id)
            .join(Site, Site.id == Device.site_id)
            .where(Alert.reseller_id == reseller_id, Alert.type.in_(types), Alert.opened_at >= since)
            .order_by(Alert.opened_at)
            .limit(500)
        )
    ).tuples()
    added = 0
    for alert, device, customer, site_name in rows:
        base = {
            "alert": {
                "id": str(alert.id),
                "type": alert.type,
                "severity": alert.severity,
                "message": alert.message,
                "opened_at": alert.opened_at.isoformat(),
                "data": alert.data,
            },
            "customer": {"erp_code": customer.erp_code, "name": customer.name, "cnpj": customer.cnpj},
            "site": site_name,
            "device": {
                "id": str(device.id),
                "serial": device.serial,
                "asset_tag": device.asset_tag,
                "brand": device.brand,
                "model": device.model,
                "sector": device.sector,
                "ip": device.ip,
                "total": device.last_total,
                "mono": device.last_mono,
                "color": device.last_color,
            },
        }
        if alert.type == "toner_low":
            sr = cfg.supply_request
            params = sr.model_dump(exclude={"enabled", "email_only"})
            payload = {**_header(cfg, "supply_request"), "request": params, **base}
            kind, key = "supply_request", f"supply:{alert.id}"
            summary = f"Requisição de suprimento — {customer.name} — {device.serial}: {alert.message}"
        else:
            category = _os_category(alert)
            so = cfg.service_order
            if category is None or category not in so.alert_types:
                continue
            code = alert.data.get("code")
            if alert.type == "printer_alert" and so.prt_alert_codes and code not in so.prt_alert_codes:
                continue
            params = so.model_dump(exclude={"enabled", "alert_types", "prt_alert_codes"})
            payload = {**_header(cfg, "service_order"), "order": {**params, "category": category}, **base}
            kind, key = "service_order", f"os:{alert.id}"
            summary = f"OS — {customer.name} — {device.serial}: {alert.message}"
        added += await _add(
            session, reseller_id, now, kind=kind, dedup_key=key, device_id=device.id, alert_id=alert.id,
            summary=summary[:500], payload=payload,
        )  # fmt: skip
    return added


async def _counters_item(
    session: AsyncSession, reseller_id: uuid.UUID, cfg: ErpConnectorSettings, now: datetime
) -> int:
    local = now.astimezone(DISPLAY_TZ)
    if local.hour < cfg.counters_hour:
        return 0
    day = local.date() - timedelta(days=1)
    key = f"counters:{day.isoformat()}"
    exists = (
        await session.execute(
            select(ErpQueueItem.id).where(
                ErpQueueItem.reseller_id == reseller_id, ErpQueueItem.dedup_key == key
            )
        )
    ).scalar_one_or_none()
    if exists:
        return 0
    before = datetime.combine(day + timedelta(days=1), time.min, DISPLAY_TZ).astimezone(UTC)
    scope = "d.deleted_at IS NULL AND d.discovery_state = 'approved' AND d.active AND d.reseller_id = :r"
    got = await cutoff(session, scope, {"r": reseller_id}, before)
    info = {
        d.id: (d, c)
        for d, c in (
            await session.execute(
                select(Device, Customer)
                .join(Customer, Customer.id == Device.customer_id)
                .where(Device.id.in_(list(got)))
            )
        ).tuples()
    } if got else {}  # fmt: skip
    readings = [
        {
            "customer_erp_code": info[c.device_id][1].erp_code,
            "serial": info[c.device_id][0].serial,
            "asset_tag": info[c.device_id][0].asset_tag,
            "read_at": c.read_at.isoformat(),
            "total": c.total,
            "mono": c.mono,
            "color": c.color,
        }
        for c in sorted(got.values(), key=lambda c: info[c.device_id][0].serial)
    ]
    payload = {**_header(cfg, "counters"), "date": day.isoformat(), "readings": readings}
    summary = f"Contadores de {day:%d/%m/%Y} — {len(readings)} equipamento(s)"
    return int(
        await _add(
            session, reseller_id, now, kind="counters", dedup_key=key, summary=summary, payload=payload
        )
    )


async def enqueue(session: AsyncSession, settings: Settings, now: datetime) -> int:
    """Worker: for every company with the connector on, queues the new alerts and the daily counters."""
    added = 0
    for row in (await session.execute(select(Setting).where(Setting.key == SETTINGS_KEY))).scalars():
        cfg = ErpConnectorSettings.model_validate(row.value or {})
        if not cfg.enabled or cfg.enabled_since is None:
            continue
        added += await _alert_items(session, row.reseller_id, cfg, cfg.enabled_since, now)
        if cfg.send_counters:
            added += await _counters_item(session, row.reseller_id, cfg, now)
    return added


# ----------------------------------------------------------------------------- transportes


class Transport(Protocol):
    name: str

    async def send(self, item: ErpQueueItem) -> None: ...


def _document(item: ErpQueueItem) -> dict[str, Any]:
    return {"id": str(item.id), "created_at": item.created_at.isoformat(), **item.payload}


@dataclass
class FileTransport:
    """One JSON file per item, written atomically (temporary name + rename) in the folder the ERP reads."""

    directory: Path
    name: str = "file"

    async def send(self, item: ErpQueueItem) -> None:
        body = json.dumps(_document(item), ensure_ascii=False, indent=2).encode("utf-8")
        stamp = item.created_at.astimezone(DISPLAY_TZ).strftime("%Y%m%d-%H%M%S")
        final = self.directory / f"{item.kind}-{stamp}-{item.id}.json"

        def _write() -> None:
            if not self.directory.is_dir():
                raise TransportError(f"a pasta {self.directory} não existe ou não é acessível")
            tmp = final.with_suffix(".tmp")
            tmp.write_bytes(body)
            os.replace(tmp, final)

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            raise TransportError(f"não foi possível gravar {final.name}: {exc}") from exc


@dataclass
class HttpTransport:
    url: str
    auth_header: str | None
    client: httpx.AsyncClient
    name: str = "http"

    async def send(self, item: ErpQueueItem) -> None:
        headers = {"Content-Type": "application/json", "Idempotency-Key": str(item.id)}
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        try:
            resp = await self.client.post(
                self.url, content=json.dumps(_document(item), ensure_ascii=False).encode(), headers=headers,
                timeout=30,
            )  # fmt: skip
        except httpx.HTTPError as exc:
            raise TransportError(f"falha de conexão com o ERP: {exc}") from exc
        if resp.status_code >= 300:  # noqa: PLR2004
            raise TransportError(f"o ERP respondeu HTTP {resp.status_code}: {resp.text[:300]}")


@dataclass
class EmailTransport:
    """'Apenas enviar e-mail' da requisição de suprimento."""

    settings: Settings
    to: str
    name: str = "email"

    async def send(self, item: ErpQueueItem) -> None:
        p = item.payload
        device, customer = p.get("device", {}), p.get("customer", {})
        body = "\n".join(
            [
                "Requisição de suprimento (Dati Monitor)",
                "",
                f"Cliente: {customer.get('name')} (código ERP {customer.get('erp_code') or '—'})",
                f"Local: {p.get('site')}",
                f"Equipamento: {device.get('brand') or ''} {device.get('model') or ''}"
                f" — série {device.get('serial')}",
                f"PAT: {device.get('asset_tag') or '—'} · Setor: {device.get('sector') or '—'}",
                f"Alerta: {p.get('alert', {}).get('message')}",
                f"Contador total: {device.get('total')}",
            ]
        )
        try:
            await SmtpNotifier(self.settings, {}).send(
                self.to, Message(subject=item.summary[:200], body=body, payload={})
            )
        except NotifyError as exc:
            raise TransportError(str(exc)) from exc


def transport_for(
    cfg: ErpConnectorSettings, item: ErpQueueItem, settings: Settings, client: httpx.AsyncClient
) -> Transport:
    if item.kind == "supply_request" and cfg.supply_request.email_only:
        if not cfg.supply_request.notify_email:
            raise TransportError("'Apenas enviar e-mail' sem e-mail de notificação")
        return EmailTransport(settings, cfg.supply_request.notify_email)
    t = cfg.transport
    if t.kind == "file":
        if not t.directory:
            raise TransportError("transporte por arquivo sem pasta configurada")
        return FileTransport(Path(t.directory))
    if not t.url:
        raise TransportError("transporte HTTP sem URL configurada")
    return HttpTransport(t.url, t.auth_header, client)


async def deliver_due(
    session: AsyncSession, settings: Settings, client: httpx.AsyncClient, now: datetime
) -> tuple[int, int]:
    """Sends the due items. Returns (sent, failed). A failure never disappears: it goes to `last_error`,
    the item waits the backoff and, after the last attempt, stays as `error` until someone retries it."""
    items = list(
        (
            await session.execute(
                select(ErpQueueItem)
                .where(ErpQueueItem.status == "pending", ErpQueueItem.next_attempt_at <= now)
                .order_by(ErpQueueItem.next_attempt_at)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    configs: dict[uuid.UUID, ErpConnectorSettings] = {}
    sent = failed = 0
    for item in items:
        if item.reseller_id not in configs:
            configs[item.reseller_id] = await load(session, settings, item.reseller_id)
        cfg = configs[item.reseller_id]
        item.attempts += 1
        try:
            if not cfg.enabled:
                raise TransportError("conector do ERP desligado nas configurações")
            transport = transport_for(cfg, item, settings, client)
            await transport.send(item)
        except TransportError as exc:
            failed += 1
            item.last_error = str(exc)[:2000]
            if item.attempts >= MAX_ATTEMPTS:
                item.status = "error"
                logger.error("fila do ERP: item %s (%s) falhou de vez: %s", item.id, item.kind, exc)
            else:
                wait = BACKOFF_MINUTES[min(item.attempts - 1, len(BACKOFF_MINUTES) - 1)]
                item.next_attempt_at = now + timedelta(minutes=wait)
                logger.warning(
                    "fila do ERP: item %s (%s) falhou (tentativa %d): %s",
                    item.id,
                    item.kind,
                    item.attempts,
                    exc,
                )
            continue
        item.status = "sent"
        item.sent_at = now
        item.last_error = None
        item.delivered_via = transport.name
        sent += 1
    await session.flush()
    return sent, failed
