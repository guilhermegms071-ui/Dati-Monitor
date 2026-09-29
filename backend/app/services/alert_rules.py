"""Alert rules, centralized in Alertas > Regras (PROMPT 16.15): one reseller-wide rule per type (created
with defaults) plus optional per-customer overrides. The customer's rule wins over the reseller's."""

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, not_found
from app.core.principal import Principal, reseller_scope
from app.models import Alert, AlertRule, Customer, NotificationChannel
from app.schemas.alerts import RULE_LABELS, AlertRuleIn, AlertRuleOut, AlertRuleUpdate, validate_params
from app.services import audit

# Regras padrão de toda revenda (canais vazios = todos os canais ativos da revenda).
DEFAULT_RULES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("agent_offline", "critical", {"minutes": 5}),
    ("device_no_reading", "warning", {"hours": 6}),
    ("toner_low", "warning", {}),
    ("toner_days_left", "info", {"days": 7, "min_confidence": 0.5}),
    ("hardware_error", "critical", {"flags": ["serviceRequested"]}),
    ("paper_jam", "warning", {}),
    ("door_open", "warning", {}),
    ("jam_recurrent", "warning", {"count": 5, "days": 3}),
    ("printer_alert", "critical", {"categories": ["service_call"]}),
    ("counter_regression", "warning", {}),
    ("suspicious_jump", "warning", {}),
    ("sum_mismatch", "info", {}),
)


async def ensure_default_rules(session: AsyncSession, reseller_id: uuid.UUID) -> int:
    """Creates the missing reseller-wide default rules. Returns how many were created."""
    existing = set(
        (
            await session.execute(
                select(AlertRule.type).where(
                    AlertRule.reseller_id == reseller_id, AlertRule.customer_id.is_(None)
                )
            )
        ).scalars()
    )
    created = 0
    for rule_type, severity, params in DEFAULT_RULES:
        if rule_type in existing:
            continue
        session.add(
            AlertRule(
                reseller_id=reseller_id,
                customer_id=None,
                name=RULE_LABELS[rule_type],
                type=rule_type,
                params=validate_params(rule_type, params),
                severity=severity,
                enabled=True,
                channel_ids=[],
            )
        )
        created += 1
    if created:
        await session.flush()
    return created


@dataclass(frozen=True)
class RuleSet:
    """Rules of one reseller indexed for evaluation."""

    reseller: dict[str, AlertRule]
    customer: dict[tuple[uuid.UUID, str], AlertRule]

    def for_customer(self, customer_id: uuid.UUID | None, rule_type: str) -> AlertRule | None:
        if customer_id is not None and (customer_id, rule_type) in self.customer:
            return self.customer[(customer_id, rule_type)]
        return self.reseller.get(rule_type)


async def load_rules(session: AsyncSession, reseller_id: uuid.UUID) -> RuleSet:
    rows = (await session.execute(select(AlertRule).where(AlertRule.reseller_id == reseller_id))).scalars()
    reseller: dict[str, AlertRule] = {}
    customer: dict[tuple[uuid.UUID, str], AlertRule] = {}
    for r in rows:
        if r.customer_id is None:
            reseller[r.type] = r
        else:
            customer[(r.customer_id, r.type)] = r
    return RuleSet(reseller, customer)


# ----------------------------------------------------------------------------- portal


async def _names(session: AsyncSession, rules: list[AlertRule]) -> dict[uuid.UUID, str]:
    ids = {r.customer_id for r in rules if r.customer_id}
    if not ids:
        return {}
    return dict(
        (await session.execute(select(Customer.id, Customer.name).where(Customer.id.in_(ids)))).tuples().all()
    )


def to_out(rule: AlertRule, names: dict[uuid.UUID, str]) -> AlertRuleOut:
    out = AlertRuleOut.model_validate(rule)
    out.type_label = RULE_LABELS.get(rule.type, rule.type)
    out.customer_name = names.get(rule.customer_id) if rule.customer_id else None
    return out


async def list_rules(
    session: AsyncSession, p: Principal, customer_id: uuid.UUID | None
) -> list[AlertRuleOut]:
    p.require("alerts.read")
    if p.customer_id is not None:
        customer_id = p.customer_id
    await ensure_default_rules(session, p.reseller_id)
    await session.commit()
    stmt = select(AlertRule).where(reseller_scope(p, AlertRule.reseller_id))
    if customer_id:
        stmt = stmt.where((AlertRule.customer_id.is_(None)) | (AlertRule.customer_id == customer_id))
    rules = list(
        (await session.execute(stmt.order_by(AlertRule.customer_id.nulls_first(), AlertRule.type))).scalars()
    )
    names = await _names(session, rules)
    return [to_out(r, names) for r in rules]


def _params(rule_type: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_params(rule_type, params)
    except ValidationError as exc:
        msg = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise bad_request(
            "invalid_rule_params", f"Parâmetros inválidos para {RULE_LABELS[rule_type]}: {msg}"
        ) from exc


async def _check_channels(session: AsyncSession, reseller_id: uuid.UUID, ids: list[uuid.UUID]) -> list[str]:
    if not ids:
        return []
    found = set(
        (
            await session.execute(
                select(NotificationChannel.id).where(
                    NotificationChannel.id.in_(ids), NotificationChannel.reseller_id == reseller_id
                )
            )
        ).scalars()
    )
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise bad_request(
            "invalid_channel", "Canal de notificação inexistente nesta revenda", channels=missing
        )
    return [str(i) for i in ids]


async def create_rule(session: AsyncSession, p: Principal, data: AlertRuleIn) -> AlertRuleOut:
    p.require("alert_rules.write")
    if data.customer_id is None:
        raise bad_request(
            "reseller_rule_exists",
            "A regra da revenda já existe para cada tipo: altere-a ou crie uma de cliente",
        )
    customer = await session.get(Customer, data.customer_id)
    if (
        customer is None
        or customer.deleted_at is not None
        or not p.can_access_customer(customer.reseller_id, customer.id)
    ):
        raise not_found("Cliente")
    dup = (
        await session.execute(
            select(AlertRule.id).where(AlertRule.customer_id == customer.id, AlertRule.type == data.type)
        )
    ).first()
    if dup:
        raise bad_request("rule_exists", "Este cliente já tem uma regra deste tipo: altere a existente")
    rule = AlertRule(
        reseller_id=customer.reseller_id,
        customer_id=customer.id,
        name=data.name,
        type=data.type,
        params=_params(data.type, data.params),
        severity=data.severity,
        enabled=data.enabled,
        channel_ids=await _check_channels(session, customer.reseller_id, data.channel_ids),
    )
    session.add(rule)
    await session.flush()
    await audit.record(
        session,
        p,
        action="create",
        entity="alert_rule",
        entity_id=rule.id,
        reseller_id=rule.reseller_id,
        after=audit.snapshot(rule),
    )
    return to_out(rule, {customer.id: customer.name})


async def _get(session: AsyncSession, p: Principal, rule_id: uuid.UUID) -> AlertRule:
    rule = await session.get(AlertRule, rule_id)
    if rule is None or not p.can_access_reseller(rule.reseller_id):
        raise not_found("Regra")
    if p.customer_id is not None and rule.customer_id != p.customer_id:
        raise not_found("Regra")
    return rule


async def update_rule(
    session: AsyncSession, p: Principal, rule_id: uuid.UUID, data: AlertRuleUpdate
) -> AlertRuleOut:
    p.require("alert_rules.write")
    rule = await _get(session, p, rule_id)
    before = audit.snapshot(rule)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if "params" in changes:
        rule.params = _params(rule.type, {**rule.params, **changes.pop("params")})
    if "channel_ids" in changes:
        rule.channel_ids = await _check_channels(session, rule.reseller_id, changes.pop("channel_ids"))
    for k, v in changes.items():
        setattr(rule, k, v)
    await session.flush()
    b, a = audit.diff(before, audit.snapshot(rule))
    await audit.record(
        session,
        p,
        action="update",
        entity="alert_rule",
        entity_id=rule.id,
        reseller_id=rule.reseller_id,
        before=b,
        after=a,
    )
    return to_out(rule, await _names(session, [rule]))


async def delete_rule(session: AsyncSession, p: Principal, rule_id: uuid.UUID) -> None:
    p.require("alert_rules.write")
    rule = await _get(session, p, rule_id)
    if rule.customer_id is None:
        raise bad_request("reseller_rule", "A regra da revenda não pode ser excluída: desative-a")
    snap = audit.snapshot(rule)
    # Alertas abertos por esta regra continuam; passam a seguir a regra da revenda.
    await session.execute(update(Alert).where(Alert.rule_id == rule.id).values(rule_id=None))
    await session.delete(rule)
    await audit.record(
        session,
        p,
        action="delete",
        entity="alert_rule",
        entity_id=rule_id,
        reseller_id=rule.reseller_id,
        before=snap,
    )
