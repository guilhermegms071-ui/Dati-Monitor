"""PostgreSQL LISTEN/NOTIFY channels (no Redis — PROMPT section 2).

`notify` runs inside the caller's transaction: PostgreSQL only delivers the notification when the
transaction commits, so a listener never sees a change that was rolled back.
"""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Payload: agent_id. Há comando novo (ou cancelado) para o coletor (ouvido pelo gateway).
CH_COMMAND = "dm_command"
# Payload: agent_id. Coletor revogado/excluído: o gateway derruba a conexão.
CH_AGENT_REVOKED = "dm_agent_revoked"
# Canais do portal ao vivo (SSE), payload JSON com reseller_id/customer_id para o filtro de escopo.
CH_COMMAND_UPDATE = "dm_command_update"  # {"type":"command","id","agent_id",...}
CH_AGENT_STATE = "dm_agent_state"  # {"type":"agent","id","state","ws_connected"?,...}
CH_DEVICES = "dm_devices"  # {"type":"devices","count",...}
CH_ALERTS = "dm_alerts"  # {"type":"alerts","opened","resolved"} — alertas abertos/resolvidos

CHANNELS = (CH_COMMAND, CH_AGENT_REVOKED, CH_COMMAND_UPDATE, CH_AGENT_STATE, CH_DEVICES, CH_ALERTS)
LIVE_CHANNELS = (CH_COMMAND_UPDATE, CH_AGENT_STATE, CH_DEVICES, CH_ALERTS)
MAX_PAYLOAD = 7900  # limite do PostgreSQL: 8000 bytes


async def notify(session: AsyncSession, channel: str, payload: str) -> None:
    if channel not in CHANNELS:
        raise ValueError(f"canal desconhecido: {channel}")
    if len(payload.encode()) > MAX_PAYLOAD:
        raise ValueError(f"payload de NOTIFY grande demais para {channel}")
    await session.execute(text("SELECT pg_notify(:ch, :payload)"), {"ch": channel, "payload": payload})


def _plain(value: Any) -> Any:
    return str(value) if isinstance(value, uuid.UUID) else value


async def notify_event(
    session: AsyncSession,
    channel: str,
    kind: str,
    *,
    reseller_id: uuid.UUID,
    customer_id: uuid.UUID | None,
    **data: Any,
) -> None:
    """Live event for the portal. Only ids and small fields travel; the portal refetches the details."""
    body = {"type": kind, "reseller_id": str(reseller_id), "customer_id": _plain(customer_id)}
    body.update({k: _plain(v) for k, v in data.items()})
    await notify(session, channel, json.dumps(body, separators=(",", ":")))
