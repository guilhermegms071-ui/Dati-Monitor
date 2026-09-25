"""Agent ↔ server protocol messages (v1). Mirror of agent/internal/protocol (Go).

docs/protocol.md and docs/protocol-schemas/*.json are generated from these models
(scripts/gen_protocol_docs.py); a test fails if they are out of date.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1


class Msg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    v: Literal[1] = 1


class EnrollRequest(Msg):
    code: str = Field(min_length=8, max_length=8, pattern=r"^[A-Z0-9]{8}$")
    hostname: str = Field(default="", max_length=255)
    os: str = Field(default="", max_length=255)
    arch: str = Field(default="", max_length=32)
    kind: Literal["windows", "linux"] = "windows"
    version: str = Field(default="", max_length=64)
    local_ips: list[str] = Field(default_factory=list, max_length=64)
    host_mac: str | None = Field(default=None, max_length=32)


class EnrollResponse(Msg):
    agent_id: str
    secret: str = Field(description="32 bytes em base64; o servidor guarda só a chave derivada")
    server_time: datetime
    ws_url: str = Field(description="Endereço do canal WebSocket (wss://…/ws/agent)")


class TokenRequest(Msg):
    agent_id: str = Field(max_length=64)
    ts: int
    nonce: str = Field(min_length=16, max_length=64, pattern=r"^[0-9a-f]+$")
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")


class TokenResponse(Msg):
    access_token: str
    expires_at: datetime
    server_time: datetime


class HeartbeatRequest(Msg):
    ts: datetime
    version: str = Field(default="", max_length=64)
    cluster_role: str = Field(default="standby", max_length=16)
    cpu_percent: float = Field(default=0, ge=0)
    memory_bytes: int = Field(default=0, ge=0)
    queue_pending: int = Field(default=0, ge=0)
    queue_dropped: int = Field(default=0, ge=0)
    uptime_seconds: int = Field(default=0, ge=0)
    local_ips: list[str] = Field(default_factory=list, max_length=64)
    hostname: str = Field(default="", max_length=255)
    os: str = Field(default="", max_length=255)
    arch: str = Field(default="", max_length=32)
    host_mac: str | None = Field(default=None, max_length=32)
    applied_config_version: int = 0
    last_scan_at: datetime | None = None
    last_read_at: datetime | None = None
    devices_known: int = 0
    paused: bool = False
    errors: list[str] = Field(default_factory=list, max_length=20)
    latency_ms: float | None = Field(default=None, ge=0, description="Ida e volta do ping no WebSocket")
    ws_connected: bool = False


class HeartbeatResponse(Msg):
    server_time: datetime
    config_version: int
    cluster_role: Literal["master", "standby"]
    paused: bool


class Intervals(BaseModel):
    discovery_minutes: int
    counters_minutes: int
    supplies_minutes: int
    status_minutes: int
    attributes_minutes: int


class DiscoveryConfig(BaseModel):
    concurrency: int
    rate_pps: int
    timeout_ms: int
    retries: int


class IpRangeConfig(BaseModel):
    id: str
    cidr: str | None = None
    start_ip: str | None = None
    end_ip: str | None = None
    exclusions: list[str] = Field(default_factory=list)
    ports: list[int] = Field(default_factory=lambda: [161])


class CredentialConfig(BaseModel):
    id: str
    version: Literal["v1", "v2c", "v3"]
    community: str | None = None
    v3_username: str | None = None
    v3_auth_protocol: str | None = None
    v3_auth_password: str | None = None
    v3_priv_protocol: str | None = None
    v3_priv_password: str | None = None


class AgentConfig(Msg):
    config_version: int
    site_id: str
    cluster_role: Literal["master", "standby"]
    paused: bool
    intervals: Intervals
    discovery: DiscoveryConfig
    ranges: list[IpRangeConfig]
    credentials: list[CredentialConfig]
    profiles: list[dict[str, Any]]
    proxy_url: str | None = None
    keep_awake: bool = False
    ws_url: str = Field(description="Endereço do canal WebSocket (wss://…/ws/agent)")


class SuggestRangesRequest(Msg):
    ranges: list[str] = Field(max_length=32)


class DeviceRef(BaseModel):
    ip: str = Field(default="", max_length=64)
    port: int = Field(default=161, ge=1, le=65535)
    serial: str = Field(default="", max_length=128)
    mac: str | None = Field(default=None, max_length=32)
    hostname: str | None = Field(default=None, max_length=255)
    sys_object_id: str | None = Field(default=None, max_length=128)
    sys_descr: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=200)
    firmware: str | None = Field(default=None, max_length=200)
    profile_key: str | None = Field(default=None, max_length=100)


class ReadingPayload(BaseModel):
    counters: dict[str, int]
    extra: dict[str, Any] = Field(default_factory=dict)
    counter_source: str = Field(default="", max_length=64)
    profile_key: str = Field(default="", max_length=100)
    profile_version: int = 0
    mono_only: bool = False
    sum_tolerance_percent: float = Field(default=2, ge=0, le=100)
    unresolved: list[str] = Field(default_factory=list)
    status: str | None = Field(default=None, max_length=16)
    error_bits: int = 0
    source: Literal["snmp", "http", "usb", "manual"] = "snmp"
    attempts: int = 0


class Supply(BaseModel):
    key: str = Field(max_length=100)
    description: str = Field(default="", max_length=255)
    type: str = Field(default="unknown", max_length=32)
    class_: Literal["consumed", "receptacle", "other"] = Field(default="other", alias="class")
    color: str | None = Field(default=None, max_length=32)
    level: int | None = None
    max_capacity: int | None = None
    percent: float | None = None
    level_state: Literal["ok", "unknown", "some_remaining"]
    unit: str | None = Field(default=None, max_length=32)

    model_config = ConfigDict(populate_by_name=True)


class Alert(BaseModel):
    severity: int = 0
    code: int = 0
    description: str = Field(default="", max_length=500)


class StatusPayload(BaseModel):
    status: Literal["ready", "printing", "warmup", "energy_saving", "warning", "error", "offline"]
    error_bits: int = 0
    reasons: list[str] = Field(default_factory=list)
    panel_text: str | None = Field(default=None, max_length=2000)
    device_status: int | None = None
    printer_status: int | None = None
    alerts: list[Alert] = Field(default_factory=list)


class EventPayload(BaseModel):
    type: Literal["read_failed"]
    data: dict[str, Any] = Field(default_factory=dict)


class Item(BaseModel):
    key: str = Field(min_length=3, max_length=200)
    kind: Literal["reading", "supplies", "status", "event"]
    read_at: datetime
    device: DeviceRef
    reading: ReadingPayload | None = None
    supplies: list[Supply] | None = None
    status: StatusPayload | None = None
    event: EventPayload | None = None


class ReadingsRequest(Msg):
    items: list[Item] = Field(max_length=500)


class ItemResult(BaseModel):
    key: str
    status: Literal["accepted", "duplicate", "discarded", "rejected"]
    reason: str | None = None


class ReadingsResponse(Msg):
    results: list[ItemResult]


# ----------------------------------------------------------------------------- comandos (seção 4.7)

CommandState = Literal["pending", "sent", "acked", "running", "succeeded", "failed", "expired", "cancelled"]
AgentReportedState = Literal["acked", "running", "succeeded", "failed"]


class CommandMessage(Msg):
    """Comando entregue ao coletor (pelo WebSocket ou por GET /api/agent/commands/pending)."""

    id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime


class CommandUpdate(Msg):
    """Andamento de um comando informado pelo coletor. Estados finais são definitivos: atualizações
    repetidas (mesmo `id`) são aceitas e ignoradas — o coletor pode reenviar sem medo."""

    id: str = Field(max_length=64)
    state: AgentReportedState
    progress: str | None = Field(default=None, max_length=2000)
    result: dict[str, Any] | None = None
    output: str | None = Field(default=None, description="Texto livre; o servidor guarda até 1 MB")
    error: str | None = Field(default=None, max_length=4000)


class CommandUpdateResponse(Msg):
    id: str
    state: CommandState


class PendingCommandsResponse(Msg):
    commands: list[CommandMessage]


class UploadResponse(Msg):
    id: str
    size_bytes: int


# ----------------------------------------------------------------------------- WebSocket /ws/agent

WsType = Literal[
    # coletor → servidor
    "hello",
    "heartbeat",
    "command_update",
    # servidor → coletor
    "welcome",
    "heartbeat_ack",
    "command",
    "cancel",
    "command_update_ack",
    "error",
]


class WsMessage(Msg):
    """Envelope de toda mensagem do WebSocket. `data` segue o modelo do tipo:
    hello → Hello; heartbeat → HeartbeatRequest; command_update → CommandUpdate;
    welcome → Welcome; heartbeat_ack → HeartbeatResponse; command → CommandMessage;
    cancel → {"id"}; command_update_ack → CommandUpdateResponse; error → {"code","message"}."""

    type: WsType
    data: dict[str, Any] = Field(default_factory=dict)


class Hello(Msg):
    version: str = Field(default="", max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=64)


class Welcome(Msg):
    agent_id: str
    server_time: datetime
    heartbeat_seconds: int = 30


PROTOCOL_MESSAGES: dict[str, type[BaseModel]] = {
    "EnrollRequest": EnrollRequest,
    "EnrollResponse": EnrollResponse,
    "TokenRequest": TokenRequest,
    "TokenResponse": TokenResponse,
    "HeartbeatRequest": HeartbeatRequest,
    "HeartbeatResponse": HeartbeatResponse,
    "AgentConfig": AgentConfig,
    "SuggestRangesRequest": SuggestRangesRequest,
    "ReadingsRequest": ReadingsRequest,
    "ReadingsResponse": ReadingsResponse,
    "CommandMessage": CommandMessage,
    "CommandUpdate": CommandUpdate,
    "CommandUpdateResponse": CommandUpdateResponse,
    "PendingCommandsResponse": PendingCommandsResponse,
    "UploadResponse": UploadResponse,
    "WsMessage": WsMessage,
    "Hello": Hello,
    "Welcome": Welcome,
}
