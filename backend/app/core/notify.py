"""PostgreSQL LISTEN/NOTIFY channels (no Redis — PROMPT section 2).

`notify` runs inside the caller's transaction: PostgreSQL only delivers the notification when the
transaction commits, so a listener never sees a command that was rolled back.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Payload: agent_id. Há comando novo (ou cancelado) para o coletor.
CH_COMMAND = "dm_command"
# Payload: agent_id. Coletor revogado/excluído: o gateway derruba a conexão.
CH_AGENT_REVOKED = "dm_agent_revoked"
# Payload: command_id. Estado de comando mudou (portal acompanha ao vivo).
CH_COMMAND_UPDATE = "dm_command_update"

CHANNELS = (CH_COMMAND, CH_AGENT_REVOKED, CH_COMMAND_UPDATE)


async def notify(session: AsyncSession, channel: str, payload: str) -> None:
    if channel not in CHANNELS:
        raise ValueError(f"canal desconhecido: {channel}")
    await session.execute(text("SELECT pg_notify(:ch, :payload)"), {"ch": channel, "payload": payload})
