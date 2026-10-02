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


class EnrollCheckRequest(Msg):
    """Instalador: confere o código antes de instalar, sem consumi-lo."""

    code: str = Field(min_length=8, max_length=8, pattern=r"^[A-Z0-9]{8}$")


class EnrollCheckResponse(Msg):
    agent_name: str
    customer_name: str
    site_name: str
    expires_at: datetime


class TokenRequest(Msg):
    agent_id: str = Field(max_length=64)
    ts: int
    nonce: str = Field(min_length=16, max_length=64, pattern=r"^[0-9a-f]+$")
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")


class TokenResponse(Msg):
    access_token: str
    expires_at: datetime
    server_time: datetime


# Estado de um serviço do produto visto pelo outro (coletor ↔ watchdog).
WatchdogServiceState = Literal["running", "stopped", "starting", "not_installed", "unknown"]


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
    install_path: str = Field(default="", max_length=1000, description="Pasta do executável do coletor")
    watchdog_state: WatchdogServiceState = Field(
        default="unknown",
        description="Serviço do dm-watchdog visto pelo coletor (vigilância mútua, seção 5.1)",
    )


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
    timeout_ms: int = Field(description="Timeout de cada consulta SNMP na descoberta")
    retries: int = Field(description="Retentativas de cada consulta (tentativas = retries + 1, de 1 a 5)")
    read_timeout_ms: int = Field(default=2000, description="Timeout de cada consulta SNMP nas leituras")


class IpRangeConfig(BaseModel):
    id: str
    cidr: str | None = None
    start_ip: str | None = None
    end_ip: str | None = None
    host: str | None = Field(default=None, description="IP ou hostname avulso (resolvido a cada varredura)")
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
    monitor_local_networks: bool = Field(
        default=False, description="Varrer também as /24 privadas das interfaces deste PC (seção 16.10)"
    )
    ignored_serials: list[str] = Field(
        default_factory=list, description="Equipamentos descartados em Descobertas: não ler (seção 16.1)"
    )


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
    sys_location: str | None = Field(default=None, max_length=255)


CounterKind = Literal["total", "print", "copy", "fax", "scan", "report", "duplex", "other"]
CounterColorMode = Literal["mono", "full_color", "single_color", "two_color", "any"]
CounterSize = Literal["a3", "a4", "letter", "legal", "other", "any"]


class CounterLine(BaseModel):
    kind: CounterKind
    color_mode: CounterColorMode
    size: CounterSize


class ReadingPayload(BaseModel):
    counters: dict[str, int]
    counter_lines: dict[str, CounterLine] = Field(
        default_factory=dict,
        description="`line` dos contadores no perfil (os nomes normalizados têm mapeamento padrão)",
    )
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
    cartridge_serial: str | None = Field(default=None, max_length=128)

    model_config = ConfigDict(populate_by_name=True)


class Alert(BaseModel):
    """Linha da prtAlertTable (RFC 3805). `index` e `time` (TimeTicks) identificam cada alerta novo."""

    index: int | None = None
    severity: int = 0
    training_level: int | None = None
    group: int | None = None
    group_index: int | None = None
    location: int | None = None
    code: int = 0
    description: str = Field(default="", max_length=500)
    time: int | None = Field(default=None, description="prtAlertTime (sysUpTime no momento, centésimos de s)")


class StatusPayload(BaseModel):
    status: Literal["ready", "printing", "warmup", "energy_saving", "warning", "error", "offline"]
    error_bits: int = 0
    reasons: list[str] = Field(default_factory=list)
    panel_text: str | None = Field(default=None, max_length=2000)
    device_status: int | None = None
    printer_status: int | None = None
    alerts: list[Alert] = Field(default_factory=list)


class StorageInfo(BaseModel):
    description: str = Field(default="", max_length=255)
    size_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)


class Subsystem(BaseModel):
    """Subsistema do equipamento (hrDeviceTable ou `attributes.subsystems` do perfil)."""

    name: str = Field(max_length=32, description="printer | copier | scanner | ou o tipo do hrDevice")
    description: str = Field(default="", max_length=255)
    status: str = Field(
        max_length=32, description="unknown | running | warning | testing | down ou texto do perfil"
    )


class Part(BaseModel):
    """Peça com nível/contador (cilindro, fusor, transferência, kit de manutenção, roletes, resíduo)."""

    name: str = Field(max_length=64)
    part: Literal[
        "drum", "fuser", "transfer", "maintenance_kit", "rollers", "waste_toner", "developer", "other"
    ]
    color: str | None = Field(default=None, max_length=32)
    unit: Literal["percent", "pages", "count"]
    value: int


class AttributesPayload(BaseModel):
    """Atributos da leitura diária (seção 16.8)."""

    firmware: list[str] = Field(default_factory=list, max_length=20)
    memory_bytes: int | None = Field(default=None, ge=0)
    storage: list[StorageInfo] = Field(default_factory=list, max_length=20)
    mac: str | None = Field(default=None, max_length=32)
    ssid: str | None = Field(default=None, max_length=64)
    uptime_seconds: int | None = Field(default=None, ge=0)
    subsystems: list[Subsystem] = Field(default_factory=list, max_length=32)
    panel_text: str | None = Field(default=None, max_length=2000)
    sys_location: str | None = Field(default=None, max_length=255)
    parts: list[Part] = Field(default_factory=list, max_length=64)


class EventPayload(BaseModel):
    type: Literal["read_failed"]
    data: dict[str, Any] = Field(default_factory=dict)


class Item(BaseModel):
    key: str = Field(min_length=3, max_length=200)
    kind: Literal["reading", "supplies", "status", "event", "attributes"]
    read_at: datetime
    device: DeviceRef
    reading: ReadingPayload | None = None
    supplies: list[Supply] | None = None
    status: StatusPayload | None = None
    event: EventPayload | None = None
    attributes: AttributesPayload | None = None


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
    "web_response",
    "web_chunk",
    "web_error",
    # servidor → coletor
    "welcome",
    "heartbeat_ack",
    "command",
    "cancel",
    "command_update_ack",
    "error",
    "web_request",
]


class WsMessage(Msg):
    """Envelope de toda mensagem do WebSocket. `data` segue o modelo do tipo:
    hello → Hello; heartbeat → HeartbeatRequest; command_update → CommandUpdate;
    welcome → Welcome; heartbeat_ack → HeartbeatResponse; command → CommandMessage;
    cancel → {"id"}; command_update_ack → CommandUpdateResponse; error → {"code","message"};
    web_request → WebRequest; web_response → WebResponseStart; web_chunk → WebChunk; web_error → WebError
    (túnel da página web da impressora, seção 4.9)."""

    type: WsType
    data: dict[str, Any] = Field(default_factory=dict)


# ----------------------------------------------------------------------------- página web da impressora (4.9)

WEB_PORTS = (80, 443, 8000, 8080, 8443)


class WebProxyOpenParams(BaseModel):
    """Parâmetros do comando `web_proxy_open`: o coletor só atende pedidos desta sessão, só para este IP e
    porta, até `expires_at`, e respeita o limite de banda."""

    session_id: str
    ip: str
    port: int
    scheme: Literal["http", "https"]
    expires_at: datetime
    max_bytes_per_second: int = Field(ge=1024)


class WebRequest(BaseModel):
    """Servidor → coletor: um pedido HTTP do navegador para a impressora (corpo inteiro em base64)."""

    stream_id: str
    session_id: str
    method: str = Field(max_length=10)
    path: str = Field(max_length=8192, description="Caminho + query, sempre começando com /")
    headers: list[tuple[str, str]] = Field(default_factory=list)
    body_b64: str | None = None


class WebResponseStart(Msg):
    """Coletor → servidor: status e cabeçalhos da resposta da impressora; o corpo vem em WebChunk."""

    stream_id: str
    status: int = Field(ge=100, le=599)
    headers: list[tuple[str, str]] = Field(default_factory=list)


class WebChunk(Msg):
    stream_id: str
    data_b64: str = ""
    end: bool = False


class WebError(Msg):
    """Coletor → servidor: o pedido não pôde ser atendido (sessão desconhecida, destino recusado, erro de
    conexão com a impressora)."""

    stream_id: str
    message: str = Field(max_length=2000)


# ----------------------------------------------------------------------------- watchdog e atualização


class WatchdogRestart(BaseModel):
    at: datetime
    reason: str = Field(max_length=500)


class WatchdogHeartbeatRequest(Msg):
    """dm-watchdog → POST /api/watchdog/heartbeat a cada 60 s (canal próprio, seção 5.1). Autentica com o
    mesmo token do coletor (mesma credencial do PC)."""

    ts: datetime
    version: str = Field(default="", max_length=64)
    os: str = Field(default="", max_length=32, description="GOOS (windows/linux)")
    arch: str = Field(default="", max_length=32, description="GOARCH (amd64/386/arm64/arm)")
    agent_state: WatchdogServiceState = "unknown"
    agent_healthy: bool = Field(default=False, description="/health do coletor respondeu saudável")
    agent_version: str = Field(default="", max_length=64)
    agent_memory_bytes: int = Field(default=0, ge=0)
    previous_agent_version: str = Field(
        default="", max_length=64, description="Versão guardada para rollback (vazio = nenhuma)"
    )
    restarts: list[WatchdogRestart] = Field(
        default_factory=list, max_length=20, description="Reinícios do coletor desde o último heartbeat"
    )
    errors: list[str] = Field(default_factory=list, max_length=20)


class WatchdogHeartbeatResponse(Msg):
    server_time: datetime
    commands: list[CommandMessage] = Field(description="Comandos do watchdog (restart_agent, update, …)")


class UpdateParams(BaseModel):
    """Parâmetros de `update` como chegam ao executor (watchdog para o coletor; coletor para o watchdog).
    A assinatura ed25519 cobre `release_message()`: componente, versão, SO, arquitetura e sha256."""

    release_id: str
    component: Literal["agent", "watchdog"]
    version: str
    os: str
    arch: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(description="ed25519 em base64")
    size_bytes: int
    url: str = Field(description="Caminho do download (GET, token do coletor)")


def release_message(component: str, version: str, os: str, arch: str, sha256: str) -> bytes:
    """Mensagem assinada de uma release (a mesma que `dm-tool sign` e o watchdog montam)."""
    return "\n".join(("dati-monitor-release/v1", component, version, os, arch, sha256)).encode()


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
    "EnrollCheckRequest": EnrollCheckRequest,
    "EnrollCheckResponse": EnrollCheckResponse,
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
    "WatchdogHeartbeatRequest": WatchdogHeartbeatRequest,
    "WatchdogHeartbeatResponse": WatchdogHeartbeatResponse,
    "WebResponseStart": WebResponseStart,
    "WebChunk": WebChunk,
    "WebError": WebError,
}

# Parâmetros de comandos (vão dentro de CommandMessage.params; não são mensagens e não levam "v").
COMMAND_PARAMS: dict[str, type[BaseModel]] = {
    "UpdateParams": UpdateParams,
    "WebProxyOpenParams": WebProxyOpenParams,
    "WebRequest": WebRequest,
}
