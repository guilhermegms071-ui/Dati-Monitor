"""Portal-facing schemas for remote commands (PROMPT 4.7): one parameter model per command type."""

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.schemas.common import ORMModel

OID_RE = re.compile(r"^\.?\d+(\.\d+)+$")


def lan_ipv4(v: str) -> str:
    """Comandos de diagnóstico só miram a rede local do cliente (privada, loopback ou link-local):
    o coletor não pode virar ferramenta para sondar a internet."""
    try:
        ip = ipaddress.IPv4Address(v.strip())
    except ValueError as exc:
        raise ValueError("IP inválido (use IPv4, ex.: 192.168.0.10)") from exc
    if not (ip.is_private or ip.is_loopback or ip.is_link_local):
        raise ValueError("só IPs da rede local do cliente (faixas privadas) são permitidos")
    return str(ip)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoParams(_Params):
    pass


class ScanNowParams(_Params):
    range_id: uuid.UUID | None = Field(default=None, description="Só esta faixa; vazio = todas")


class ReadNowParams(_Params):
    device_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=500, description="Só estes equipamentos; vazio = todos"
    )


class TargetParams(_Params):
    ip: str
    port: int = Field(default=161, ge=1, le=65535)

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        return lan_ipv4(v)


class MibWalkParams(TargetParams):
    root_oid: str | None = Field(default=None, max_length=255, description="Subárvore; vazio = walk completo")

    @field_validator("root_oid")
    @classmethod
    def validate_oid(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        if not OID_RE.match(v.strip()):
            raise ValueError("OID inválido (ex.: 1.3.6.1.2.1.43)")
        return v.strip().lstrip(".")


class GetLogsParams(_Params):
    hours: int = Field(default=24, ge=1, le=168)


class WakeHostParams(_Params):
    target_agent_id: uuid.UUID = Field(description="Coletor (do mesmo local) cujo PC será ligado")


class PingHostParams(_Params):
    ip: str
    ports: list[int] = Field(default_factory=lambda: [80, 443, 9100], max_length=16)

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        return lan_ipv4(v)

    @field_validator("ports")
    @classmethod
    def validate_ports(cls, v: list[int]) -> list[int]:
        if any(p < 1 or p > 65535 for p in v):  # noqa: PLR2004
            raise ValueError("porta fora do intervalo 1 a 65535")
        return sorted(set(v))


CommandType = Literal[
    "reconnect",
    "restart_watchdog",
    "scan_now",
    "read_now",
    "read_device",
    "snmp_test",
    "mib_walk",
    "set_config",
    "get_logs",
    "diagnostics",
    "pause",
    "resume",
    "promote_master",
    "wake_host",
    "ping_host",
]

PARAMS_BY_TYPE: dict[str, type[_Params]] = {
    "reconnect": NoParams,
    "restart_watchdog": NoParams,
    "scan_now": ScanNowParams,
    "read_now": ReadNowParams,
    "read_device": TargetParams,
    "snmp_test": TargetParams,
    "mib_walk": MibWalkParams,
    "set_config": NoParams,
    "get_logs": GetLogsParams,
    "diagnostics": NoParams,
    "pause": NoParams,
    "resume": NoParams,
    "promote_master": NoParams,
    "wake_host": WakeHostParams,
    "ping_host": PingHostParams,
}

COMMAND_LABELS: dict[str, str] = {
    "reconnect": "Reconectar",
    "restart_watchdog": "Reiniciar o watchdog",
    "scan_now": "Varrer a rede agora",
    "read_now": "Ler agora",
    "read_device": "Ler um equipamento",
    "snmp_test": "Testar SNMP",
    "mib_walk": "Walk SNMP",
    "set_config": "Aplicar configuração",
    "get_logs": "Baixar logs",
    "diagnostics": "Diagnóstico",
    "pause": "Pausar coletas",
    "resume": "Retomar coletas",
    "promote_master": "Tornar MASTER",
    "wake_host": "Ligar PC (Wake-on-LAN)",
    "ping_host": "Ping",
}


class CommandIn(BaseModel):
    type: CommandType
    params: dict[str, Any] = Field(default_factory=dict)
    expires_in_minutes: int | None = Field(default=None, ge=1, le=1440)


class CommandOut(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    target: str
    type: str
    params: dict[str, Any]
    state: str
    result: dict[str, Any] | None
    output: str | None
    progress: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    sent_at: datetime | None
    acked_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    expires_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def type_label(self) -> str:
        """Nome do comando em português (o portal nunca mostra o código cru)."""
        return COMMAND_LABELS.get(self.type, self.type)


class AgentLogOut(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    command_id: uuid.UUID | None
    source: str
    size_bytes: int
    hours: int | None
    created_at: datetime


class MibWalkOut(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    device_id: uuid.UUID | None
    command_id: uuid.UUID | None
    ip: str
    root_oid: str | None
    oid_count: int
    created_at: datetime
