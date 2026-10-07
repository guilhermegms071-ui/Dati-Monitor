"""Alert evaluation (worker, every minute — PROMPT 8): applies the rules, opens one alert per condition
(de-duplicated by `dedup_key`) and resolves automatically the alerts whose condition is gone.

Grouping (PROMPT 9): when every collector of a site is offline, its devices do not raise "sem leitura"
alerts — the collector alert already covers the whole site.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Alert, Customer, Device, PrinterAlert, Reseller, Site, SupplyCurrent
from app.models.tenancy import DEFAULT_TONER_THRESHOLDS
from app.services.alert_rules import RuleSet, ensure_default_rules, load_rules
from app.services.alerts import open_alert

logger = logging.getLogger(__name__)

SP = ZoneInfo("America/Sao_Paulo")
# Tipos cujo ciclo de vida (abrir e resolver) é do motor. Os da ingestão (regressão, salto, soma) são
# classificados pelo operador e nunca resolvidos sozinhos.
ENGINE_TYPES = frozenset(
    {
        "agent_offline",
        "device_no_reading",
        "toner_low",
        "toner_days_left",
        "hardware_error",
        "paper_jam",
        "door_open",
        "jam_recurrent",
        "printer_alert",
    }
)
TONER_TYPES = ("toner", "tonerCartridge", "ink", "inkCartridge")
COLOR_LABEL = {"black": "preto", "cyan": "ciano", "magenta": "magenta", "yellow": "amarelo"}
FLAG_TYPES = {"paper_jam": "jammed", "door_open": "doorOpen"}
CATEGORY_LABEL = {
    "parts": "Peças/manutenção",
    "service_call": "Chamado técnico",
    "jam": "Atolamento",
    "consumable": "Consumível",
    "other": "Outros",
}


def fmt_sp(dt: datetime | None) -> str:
    return dt.astimezone(SP).strftime("%d/%m %H:%M") if dt else "nunca"


@dataclass
class Condition:
    type: str
    dedup_key: str
    customer_id: uuid.UUID | None
    site_id: uuid.UUID | None
    target_type: str
    target_id: uuid.UUID
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    opened: int = 0
    resolved: int = 0
    # (revenda, cliente) com alertas abertos ou resolvidos nesta rodada: evento ao vivo do portal.
    touched: set[tuple[uuid.UUID, uuid.UUID | None]] = field(default_factory=set)


def device_label(d: Device) -> str:
    name = " ".join(x for x in (d.brand, d.model) if x) or "equipamento"
    return f"{name} {d.serial}" + (f" ({d.sector})" if d.sector else "")


def thresholds_for(device: Device, customer: Customer) -> dict[str, Any] | None:
    """Limiar de toner por cor do equipamento (seção 16.5); None = monitoramento desligado."""
    if device.toner_mode == "off":
        return None
    if device.toner_mode == "individual" and device.toner_thresholds:
        return {**DEFAULT_TONER_THRESHOLDS, **device.toner_thresholds}
    if not customer.toner_monitoring:
        return None
    return {**DEFAULT_TONER_THRESHOLDS, **(customer.toner_thresholds or {})}


async def _agent_conditions(
    session: AsyncSession, rules: RuleSet, reseller_id: uuid.UUID, now: datetime
) -> tuple[list[Condition], set[uuid.UUID]]:
    rows = (
        await session.execute(
            select(Agent, Site.customer_id)
            .join(Site, Site.id == Agent.site_id)
            .where(
                Agent.reseller_id == reseller_id,
                Agent.deleted_at.is_(None),
                Agent.revoked_at.is_(None),
                Agent.enrolled_at.is_not(None),
                Site.deleted_at.is_(None),
            )
        )
    ).tuples()
    conds: list[Condition] = []
    online_sites: set[uuid.UUID] = set()
    sites: set[uuid.UUID] = set()
    for agent, customer_id in rows:
        sites.add(agent.site_id)
        if agent.uninstalled_at is not None:
            continue  # desinstalado: já tem o alerta próprio (agent_uninstalled), não "sem sinal"
        if agent.state != "offline":
            online_sites.add(agent.site_id)
            continue
        rule = rules.for_customer(customer_id, "agent_offline")
        if rule is None or not rule.enabled or agent.paused:
            continue
        minutes = int(rule.params.get("minutes", 5))
        if agent.last_seen_at is not None and agent.last_seen_at > now - timedelta(minutes=minutes):
            continue
        conds.append(
            Condition(
                "agent_offline",
                f"agent_offline:{agent.id}",
                customer_id,
                agent.site_id,
                "agent",
                agent.id,
                f"{agent.name} sem sinal desde {fmt_sp(agent.last_seen_at)}",
                {
                    "agent_name": agent.name,
                    "last_seen_at": agent.last_seen_at.isoformat() if agent.last_seen_at else None,
                    "hostname": agent.hostname,
                },
            )
        )
    return conds, sites - online_sites


async def _device_conditions(
    session: AsyncSession, rules: RuleSet, reseller_id: uuid.UUID, now: datetime, dark_sites: set[uuid.UUID]
) -> list[Condition]:
    rows = (
        await session.execute(
            select(Device, Customer)
            .join(Customer, Customer.id == Device.customer_id)
            .where(
                Device.reseller_id == reseller_id,
                Device.deleted_at.is_(None),
                Device.active.is_(True),
                Device.monitored.is_(True),
                Device.discovery_state == "approved",
            )
        )
    ).tuples()
    conds: list[Condition] = []
    for device, customer in rows:
        base = {"serial": device.serial, "model": device.model, "ip": device.ip}
        rule = rules.for_customer(customer.id, "device_no_reading")
        if rule is not None and rule.enabled and device.site_id not in dark_sites:
            limit = now - timedelta(hours=int(rule.params.get("hours", 6)))
            last = device.last_read_at or device.first_seen_at
            if last < limit:
                conds.append(
                    Condition(
                        "device_no_reading",
                        f"device_no_reading:{device.id}",
                        customer.id,
                        device.site_id,
                        "device",
                        device.id,
                        f"{device_label(device)} sem leitura desde {fmt_sp(device.last_read_at)}",
                        {
                            **base,
                            "last_read_at": device.last_read_at.isoformat() if device.last_read_at else None,
                        },
                    )
                )
        # Erros do status atual; equipamento sem resposta não tem status confiável.
        reasons = set(device.last_error_reasons or [])
        if not reasons or device.last_status == "offline" or device.disconnected:
            continue
        for rule_type, flag in FLAG_TYPES.items():
            r = rules.for_customer(customer.id, rule_type)
            if r is not None and r.enabled and flag in reasons:
                label = "Atolamento de papel" if rule_type == "paper_jam" else "Porta aberta"
                conds.append(
                    Condition(
                        rule_type,
                        f"{rule_type}:{device.id}",
                        customer.id,
                        device.site_id,
                        "device",
                        device.id,
                        f"{label} em {device_label(device)}",
                        {**base, "reasons": sorted(reasons)},
                    )
                )
        r = rules.for_customer(customer.id, "hardware_error")
        if r is not None and r.enabled:
            hit = sorted(reasons & set(r.params.get("flags", [])))
            if hit:
                conds.append(
                    Condition(
                        "hardware_error",
                        f"hardware_error:{device.id}",
                        customer.id,
                        device.site_id,
                        "device",
                        device.id,
                        f"Erro de hardware em {device_label(device)}: {', '.join(hit)}",
                        {**base, "flags": hit, "panel_text": device.last_panel_text},
                    )
                )
    return conds


async def _supply_conditions(
    session: AsyncSession, rules: RuleSet, reseller_id: uuid.UUID
) -> list[Condition]:
    rows = (
        await session.execute(
            select(SupplyCurrent, Device, Customer)
            .join(Device, Device.id == SupplyCurrent.device_id)
            .join(Customer, Customer.id == Device.customer_id)
            .where(
                SupplyCurrent.reseller_id == reseller_id,
                SupplyCurrent.supply_class == "consumed",
                SupplyCurrent.supply_type.in_(TONER_TYPES),
                Device.deleted_at.is_(None),
                Device.active.is_(True),
                Device.monitored.is_(True),
                Device.discovery_state == "approved",
            )
        )
    ).tuples()
    conds: list[Condition] = []
    for sup, device, customer in rows:
        thresholds = thresholds_for(device, customer)
        if thresholds is None:
            continue  # monitoramento de suprimentos desligado (16.5)
        color = sup.color or "black"
        name = COLOR_LABEL.get(color, color)
        base = {"serial": device.serial, "model": device.model, "supply_key": sup.supply_key, "color": color}
        low = rules.for_customer(customer.id, "toner_low")
        limit = Decimal(str(thresholds.get(color, thresholds.get("black", 10))))
        if low is not None and low.enabled and sup.percent is not None and sup.percent <= limit:
            conds.append(
                Condition(
                    "toner_low",
                    f"toner_low:{device.id}:{sup.supply_key}",
                    customer.id,
                    device.site_id,
                    "device",
                    device.id,
                    f"Toner {name} em {sup.percent:.0f}% em {device_label(device)} (limiar {limit:.0f}%)",
                    {**base, "percent": float(sup.percent), "threshold": float(limit)},
                )
            )
        days = rules.for_customer(customer.id, "toner_days_left")
        if (
            days is not None
            and days.enabled
            and sup.days_to_empty is not None
            and sup.forecast_confidence is not None
            and float(sup.forecast_confidence) >= float(days.params.get("min_confidence", 0.5))
            and sup.days_to_empty <= int(days.params.get("days", 7))
        ):
            conds.append(
                Condition(
                    "toner_days_left",
                    f"toner_days_left:{device.id}:{sup.supply_key}",
                    customer.id,
                    device.site_id,
                    "device",
                    device.id,
                    f"Toner {name} de {device_label(device)} acaba em cerca de "
                    f"{sup.days_to_empty:.0f} dia(s)",
                    {
                        **base,
                        "days_to_empty": float(sup.days_to_empty),
                        "days_min": float(sup.days_to_empty_min)
                        if sup.days_to_empty_min is not None
                        else None,
                        "days_max": float(sup.days_to_empty_max)
                        if sup.days_to_empty_max is not None
                        else None,
                        "confidence": float(sup.forecast_confidence),
                    },
                )
            )
    return conds


async def _printer_conditions(
    session: AsyncSession, rules: RuleSet, reseller_id: uuid.UUID, now: datetime
) -> list[Condition]:
    conds: list[Condition] = []
    devices = {
        d.id: d
        for d in (
            await session.execute(
                select(Device).where(
                    Device.reseller_id == reseller_id,
                    Device.deleted_at.is_(None),
                    Device.active.is_(True),
                    Device.monitored.is_(True),
                    Device.discovery_state == "approved",
                )
            )
        ).scalars()
    }
    if not devices:
        return conds
    # Atolamento recorrente: N atolamentos em X dias. A janela maior entre as regras limita a consulta.
    windows = [r for r in (*rules.reseller.values(), *rules.customer.values()) if r.type == "jam_recurrent"]
    max_days = max((int(r.params.get("days", 3)) for r in windows), default=0)
    if max_days:
        jams = (
            await session.execute(
                select(PrinterAlert.device_id, PrinterAlert.first_seen_at).where(
                    PrinterAlert.reseller_id == reseller_id,
                    PrinterAlert.category == "jam",
                    PrinterAlert.first_seen_at >= now - timedelta(days=max_days),
                )
            )
        ).tuples()
        by_device: dict[uuid.UUID, list[datetime]] = {}
        for device_id, seen in jams:
            by_device.setdefault(device_id, []).append(seen)
        for device_id, stamps in by_device.items():
            device = devices.get(device_id)
            if device is None:
                continue
            rule = rules.for_customer(device.customer_id, "jam_recurrent")
            if rule is None or not rule.enabled:
                continue
            days, count = int(rule.params.get("days", 3)), int(rule.params.get("count", 5))
            n = sum(1 for s in stamps if s >= now - timedelta(days=days))
            if n >= count:
                conds.append(
                    Condition(
                        "jam_recurrent",
                        f"jam_recurrent:{device.id}",
                        device.customer_id,
                        device.site_id,
                        "device",
                        device.id,
                        f"Atolamento recorrente em {device_label(device)}: {n} em {days} dia(s)",
                        {"serial": device.serial, "count": n, "days": days},
                    )
                )
    active = (
        await session.execute(
            select(PrinterAlert).where(
                PrinterAlert.reseller_id == reseller_id, PrinterAlert.cleared_at.is_(None)
            )
        )
    ).scalars()
    for pa in active:
        device = devices.get(pa.device_id)
        if device is None:
            continue
        rule = rules.for_customer(device.customer_id, "printer_alert")
        if rule is None or not rule.enabled or pa.category not in rule.params.get("categories", []):
            continue
        conds.append(
            Condition(
                "printer_alert",
                f"printer_alert:{pa.id}",
                device.customer_id,
                device.site_id,
                "device",
                device.id,
                f"{CATEGORY_LABEL[pa.category]} em {device_label(device)}: "
                f"{pa.description or f'código {pa.code}'}",
                {
                    "serial": device.serial,
                    "category": pa.category,
                    "code": pa.code,
                    "printer_alert_id": str(pa.id),
                },
            )
        )
    return conds


async def evaluate_reseller(session: AsyncSession, reseller_id: uuid.UUID, now: datetime) -> EvalResult:
    await ensure_default_rules(session, reseller_id)
    rules = await load_rules(session, reseller_id)
    agent_conds, dark_sites = await _agent_conditions(session, rules, reseller_id, now)
    conds = [
        *agent_conds,
        *await _device_conditions(session, rules, reseller_id, now, dark_sites),
        *await _supply_conditions(session, rules, reseller_id),
        *await _printer_conditions(session, rules, reseller_id, now),
    ]
    result = EvalResult()
    keys: set[str] = set()
    for c in conds:
        keys.add(c.dedup_key)
        rule = rules.for_customer(c.customer_id, c.type)
        if rule is None:
            continue
        opened = await open_alert(
            session,
            reseller_id=reseller_id,
            type_=c.type,
            severity=rule.severity,
            target_type=c.target_type,
            target_id=c.target_id,
            message=c.message,
            dedup_key=c.dedup_key,
            customer_id=c.customer_id,
            site_id=c.site_id,
            rule_id=rule.id,
            data=c.data,
        )
        if opened:
            result.opened += 1
            result.touched |= {(reseller_id, c.customer_id), (reseller_id, None)}
    stale = await session.execute(
        update(Alert)
        .where(
            Alert.reseller_id == reseller_id,
            Alert.state != "resolved",
            Alert.type.in_(ENGINE_TYPES),
            Alert.dedup_key.not_in(keys) if keys else Alert.id.is_not(None),
        )
        .values(
            state="resolved",
            resolved_at=now,
            data=Alert.data.op("||")(func.jsonb_build_object("auto_resolved", True)),
        )
        .returning(Alert.customer_id)
    )
    for customer_id in stale.scalars():
        result.resolved += 1
        result.touched |= {(reseller_id, customer_id), (reseller_id, None)}
    return result


async def evaluate(session: AsyncSession, now: datetime | None = None) -> EvalResult:
    now = now or datetime.now(UTC)
    total = EvalResult()
    resellers = (
        (await session.execute(select(Reseller.id).where(Reseller.deleted_at.is_(None)))).scalars().all()
    )
    for reseller_id in resellers:
        r = await evaluate_reseller(session, reseller_id, now)
        total.opened += r.opened
        total.resolved += r.resolved
        total.touched |= r.touched
    return total
