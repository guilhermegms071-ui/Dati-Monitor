"""Portal schemas for collectors (agents), IP ranges, SNMP credentials, collection settings and devices."""

import ipaddress
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ORMModel


class CollectionConfig(BaseModel):
    """Configuração de coleta de um Local (PROMPT 4.5/4.6). Campos ausentes usam o padrão do sistema."""

    discovery_minutes: int | None = Field(default=None, ge=15, le=10080)
    counters_minutes: int | None = Field(default=None, ge=5, le=1440)
    supplies_minutes: int | None = Field(default=None, ge=5, le=1440)
    status_minutes: int | None = Field(default=None, ge=1, le=1440)
    attributes_minutes: int | None = Field(default=None, ge=60, le=10080)
    discovery_concurrency: int | None = Field(default=None, ge=1, le=256)
    discovery_rate_pps: int | None = Field(default=None, ge=10, le=5000)
    snmp_timeout_ms: int | None = Field(default=None, ge=500, le=10000)
    snmp_retries: int | None = Field(default=None, ge=0, le=5)
    keep_awake: bool | None = None
    proxy_url: str | None = Field(default=None, max_length=500)

    @field_validator("proxy_url")
    @classmethod
    def _proxy(cls, v: str | None) -> str | None:
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("proxy deve começar com http:// ou https://")
        return v or None


DEFAULT_COLLECTION = {
    "discovery_minutes": 360,
    "counters_minutes": 60,
    "supplies_minutes": 60,
    "status_minutes": 10,
    "attributes_minutes": 1440,
    "discovery_concurrency": 64,
    "discovery_rate_pps": 200,
    "snmp_timeout_ms": 1500,
    "snmp_retries": 1,
    "keep_awake": False,
    "proxy_url": None,
}


# ----------------------------------------------------------------------------- agents


class AgentIn(BaseModel):
    site_id: uuid.UUID
    name: str = Field(min_length=2, max_length=200)
    kind: Literal["windows", "linux"] = "windows"
    update_channel: Literal["canary", "stable"] = "stable"
    priority: int = Field(default=100, ge=1, le=1000)


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    update_channel: Literal["canary", "stable"] | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)


class AgentOut(ORMModel):
    id: uuid.UUID
    reseller_id: uuid.UUID
    site_id: uuid.UUID
    name: str
    kind: str
    hostname: str | None
    os: str | None
    arch: str | None
    local_ips: list[Any]
    host_mac: str | None
    version: str | None
    watchdog_version: str | None
    update_channel: str
    cluster_role: str
    priority: int
    state: str
    last_seen_at: datetime | None
    last_watchdog_seen_at: datetime | None
    enrolled_at: datetime | None
    revoked_at: datetime | None
    config_version: int
    applied_config_version: int
    queue_pending: int
    uptime_seconds: int | None
    cpu_percent: float | None
    memory_bytes: int | None
    last_error: str | None
    suggested_ranges: list[Any]
    created_at: datetime
    updated_at: datetime


class EnrollmentCodeOut(BaseModel):
    code: str
    expires_at: datetime
    install_command: str
    instructions: list[str]


class AgentCreated(BaseModel):
    agent: AgentOut
    enrollment: EnrollmentCodeOut


# ----------------------------------------------------------------------------- IP ranges


def _ipv4(v: str) -> str:
    try:
        ip = ipaddress.IPv4Address(v.strip())
    except ValueError as exc:
        raise ValueError(f"IPv4 inválido: {v}") from exc
    return str(ip)


def _exclusion(v: str) -> str:
    v = v.strip()
    try:
        if "/" in v:
            return str(ipaddress.IPv4Network(v, strict=False))
        if "-" in v:
            a, b = v.split("-", 1)
            if ipaddress.IPv4Address(a.strip()) > ipaddress.IPv4Address(b.strip()):
                raise ValueError("início maior que o fim")
            return f"{a.strip()}-{b.strip()}"
        return str(ipaddress.IPv4Address(v))
    except ValueError as exc:
        raise ValueError(f"exclusão inválida: {v} ({exc})") from exc


class IpRangeIn(BaseModel):
    cidr: str | None = None
    start_ip: str | None = None
    end_ip: str | None = None
    exclusions: list[str] = Field(default_factory=list, max_length=200)
    ports: list[int] = Field(default_factory=lambda: [161], min_length=1, max_length=16)
    active: bool = True

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            net = ipaddress.IPv4Network(v.strip(), strict=False)
        except ValueError as exc:
            raise ValueError(f"CIDR inválido: {v}") from exc
        if net.prefixlen < 16:  # noqa: PLR2004
            raise ValueError("a faixa pode ter no máximo /16 (65.536 endereços)")
        return str(net)

    @field_validator("start_ip", "end_ip")
    @classmethod
    def _ip(cls, v: str | None) -> str | None:
        return _ipv4(v) if v else None

    @field_validator("exclusions")
    @classmethod
    def _excl(cls, v: list[str]) -> list[str]:
        return [_exclusion(x) for x in v if x.strip()]

    @field_validator("ports")
    @classmethod
    def _ports(cls, v: list[int]) -> list[int]:
        for p in v:
            if not 1 <= p <= 65535:  # noqa: PLR2004
                raise ValueError(f"porta inválida: {p}")
        return sorted(set(v))

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        if bool(self.cidr) == bool(self.start_ip or self.end_ip):
            raise ValueError("informe CIDR ou início/fim (um dos dois)")
        if not self.cidr:
            if not (self.start_ip and self.end_ip):
                raise ValueError("informe início e fim")
            a, b = ipaddress.IPv4Address(self.start_ip), ipaddress.IPv4Address(self.end_ip)
            if a > b:
                raise ValueError("o início é maior que o fim")
            if int(b) - int(a) >= 65536:  # noqa: PLR2004
                raise ValueError("intervalo grande demais (máximo 65.536 endereços)")
        return self


class IpRangeOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    cidr: str | None
    start_ip: str | None
    end_ip: str | None
    exclusions: list[Any]
    ports: list[Any]
    active: bool
    status: str
    suggested_by_agent_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------------------- SNMP credentials


class SnmpCredentialIn(BaseModel):
    version: Literal["v1", "v2c", "v3"]
    position: int | None = Field(default=None, ge=1, le=100)
    community: str | None = Field(default=None, min_length=1, max_length=128)
    v3_username: str | None = Field(default=None, min_length=1, max_length=128)
    v3_auth_protocol: Literal["SHA", "SHA256"] | None = None
    v3_auth_password: str | None = Field(default=None, min_length=8, max_length=128)
    v3_priv_protocol: Literal["AES", "AES256"] | None = None
    v3_priv_password: str | None = Field(default=None, min_length=8, max_length=128)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.version in ("v1", "v2c"):
            if not self.community:
                raise ValueError("comunidade obrigatória para v1/v2c")
        else:
            if not self.v3_username:
                raise ValueError("usuário obrigatório para SNMPv3")
            if self.v3_auth_protocol and not self.v3_auth_password:
                raise ValueError("senha de autenticação obrigatória")
            if self.v3_priv_protocol and not (self.v3_auth_protocol and self.v3_priv_password):
                raise ValueError("criptografia exige autenticação e senha de criptografia")
        return self


class SnmpCredentialUpdate(BaseModel):
    position: int | None = Field(default=None, ge=1, le=100)
    community: str | None = Field(default=None, min_length=1, max_length=128)
    v3_username: str | None = Field(default=None, min_length=1, max_length=128)
    v3_auth_protocol: Literal["SHA", "SHA256"] | None = None
    v3_auth_password: str | None = Field(default=None, min_length=8, max_length=128)
    v3_priv_protocol: Literal["AES", "AES256"] | None = None
    v3_priv_password: str | None = Field(default=None, min_length=8, max_length=128)


class SnmpCredentialOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID
    position: int
    version: str
    has_community: bool
    community_hint: str | None = Field(description="Primeiro e último caractere, para conferência")
    v3_username: str | None
    v3_auth_protocol: str | None
    v3_priv_protocol: str | None
    has_auth_password: bool
    has_priv_password: bool
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------------------- devices (Fase 2: leitura)


class DeviceOut(ORMModel):
    id: uuid.UUID
    reseller_id: uuid.UUID
    site_id: uuid.UUID
    customer_id: uuid.UUID
    serial: str
    mac: str | None
    ip: str | None
    snmp_port: int
    hostname: str | None
    brand: str | None
    model: str | None
    sys_object_id: str | None
    firmware: str | None
    asset_tag: str | None
    sector: str | None
    notes: str | None
    is_color: bool | None
    profile_key: str | None
    counter_source: str | None
    source: str
    first_seen_at: datetime
    last_read_at: datetime | None
    last_status: str
    last_status_at: datetime | None
    last_error_bits: int | None
    last_error_reasons: list[Any]
    last_panel_text: str | None
    last_total: int | None
    last_mono: int | None
    last_color: int | None
    disconnected: bool
    active: bool
    monitored: bool
    last_agent_id: uuid.UUID | None


class ReadingOut(ORMModel):
    id: uuid.UUID
    device_id: uuid.UUID
    agent_id: uuid.UUID | None
    read_at: datetime
    received_at: datetime
    total: int | None
    mono: int | None
    color: int | None
    mono_large: int | None
    color_large: int | None
    copy_mono: int | None
    copy_color: int | None
    print_mono: int | None
    print_color: int | None
    scan: int | None
    fax: int | None
    status: str | None
    error_bits: int | None
    source: str
    profile_key: str | None
    counter_source: str | None
    flags: list[Any]
    extra: dict[str, Any]


class SupplyOut(ORMModel):
    supply_key: str
    read_at: datetime
    description: str | None
    supply_type: str | None
    supply_class: str
    color: str | None
    level: int | None
    max_capacity: int | None
    percent: Decimal | None
    level_state: str
    unit: str | None
    days_to_empty: Decimal | None


class DeviceEventOut(ORMModel):
    id: uuid.UUID
    device_id: uuid.UUID
    type: str
    data: dict[str, Any]
    user_id: uuid.UUID | None
    created_at: datetime
