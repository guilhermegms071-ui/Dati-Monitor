"""Printer alerts from prtAlertTable (RFC 3805; PROMPT 16.4): one row per new alert, classified, with the
device counters at that moment; alerts that leave the table are marked cleared."""

import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, PrinterAlert
from app.schemas import agent as proto

# PrtAlertCodeTC (RFC 3805).
CODE_JAM = 8
SERVICE_CALL_CODES = frozenset(
    {
        30,  # subunitUnrecoverableFailure
        32,  # subunitUnrecoverableStorageError
        33,  # subunitMotorFailure
        35,  # subunitUnderTemperature
        36,  # subunitOverTemperature
        37,  # subunitTimingFailure
        38,  # subunitThermistorFailure
        1001,  # markerFuserUnderTemperature
        1002,  # markerFuserOverTemperature
        1003,  # markerFuserTimingFailure
        1004,  # markerFuserThermistorFailure
    }
)
CONSUMABLE_CODES = frozenset(
    {
        1101,  # markerTonerEmpty
        1102,  # markerInkEmpty
        1103,  # markerPrintRibbonEmpty
        1104,  # markerTonerAlmostEmpty
        1105,  # markerInkAlmostEmpty
        1106,  # markerPrintRibbonAlmostEmpty
        1107,  # markerWasteTonerReceptacleAlmostFull
        1108,  # markerWasteInkReceptacleAlmostFull
        1109,  # markerWasteTonerReceptacleFull
        1110,  # markerWasteInkReceptacleFull
        1113,  # markerDeveloperAlmostEmpty
        1114,  # markerDeveloperEmpty
        1115,  # markerTonerCartridgeMissing
    }
)
# subunitAlmostEmpty/Empty/AlmostFull/Full só contam como consumível nos grupos de suprimento.
SUPPLY_LEVEL_CODES = frozenset({12, 13, 14, 15})
SUPPLY_GROUPS = frozenset({11, 12})  # markerSupplies, markerColorant
PARTS_CODES = frozenset(
    {
        10,  # subunitLifeAlmostOver
        11,  # subunitLifeOver
        16,  # subunitNearLimit
        17,  # subunitAtLimit
        1111,  # markerOpcLifeAlmostOver
        1112,  # markerOpcLifeOver
    }
)
TRAINING_FIELD_SERVICE = 5


def classify(alert: proto.Alert) -> str:
    """Peças/manutenção, Chamado técnico, Atolamento, Consumível ou Outros (ordem de precedência)."""
    if alert.code == CODE_JAM:
        return "jam"
    if alert.code in SERVICE_CALL_CODES:
        return "service_call"
    if alert.code in CONSUMABLE_CODES or (alert.code in SUPPLY_LEVEL_CODES and alert.group in SUPPLY_GROUPS):
        return "consumable"
    if alert.code in PARTS_CODES:
        return "parts"
    if alert.training_level == TRAINING_FIELD_SERVICE:
        return "service_call"
    return "other"


def alert_key(alert: proto.Alert) -> str:
    """Identity of one alert occurrence: prtAlertTime changes for every new alert, even on the same index."""
    raw = "|".join(
        str(v)
        for v in (
            alert.index,
            alert.code,
            alert.group,
            alert.group_index,
            alert.location,
            alert.time,
            alert.description.strip(),
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()


async def sync_alerts(
    session: AsyncSession, device: Device, alerts: list[proto.Alert], read_at: datetime
) -> list[PrinterAlert]:
    """Inserts new alerts, refreshes the ones still present and clears the ones gone. Returns the new."""
    active = {
        a.alert_key: a
        for a in (
            await session.execute(
                select(PrinterAlert).where(
                    PrinterAlert.device_id == device.id, PrinterAlert.cleared_at.is_(None)
                )
            )
        ).scalars()
    }
    seen: set[str] = set()
    created: list[PrinterAlert] = []
    for alert in alerts:
        key = alert_key(alert)
        if key in seen:
            continue
        seen.add(key)
        row = active.get(key)
        if row is not None:
            row.last_seen_at = max(row.last_seen_at, read_at)
            continue
        row = PrinterAlert(
            reseller_id=device.reseller_id,
            device_id=device.id,
            alert_key=key,
            prt_index=alert.index,
            severity=alert.severity,
            training_level=alert.training_level,
            group=alert.group,
            group_index=alert.group_index,
            location=alert.location,
            code=alert.code,
            description=alert.description.strip() or None,
            alert_time_ticks=alert.time,
            category=classify(alert),
            first_seen_at=read_at,
            last_seen_at=read_at,
            total_at=device.last_total,
            mono_at=device.last_mono,
            color_at=device.last_color,
        )
        session.add(row)
        created.append(row)
    for key, row in active.items():
        if key not in seen:
            row.cleared_at = read_at
    return created
