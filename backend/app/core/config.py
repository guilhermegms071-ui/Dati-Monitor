"""Settings from environment variables (and the repository .env in development)."""

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.product import REPO_ROOT

RELEASE_PUBLIC_KEY_FILE = REPO_ROOT / "agent" / "internal" / "release" / "public.key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: str = Field(min_length=1)
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    db_check_interval_seconds: int = Field(default=30, ge=1)

    # Segredos (sempre por variável de ambiente; nunca no código).
    jwt_secret: SecretStr = Field(min_length=32)
    master_key: SecretStr = Field(description="Chave AES-256-GCM em base64 (32 bytes)")

    # Autenticação do portal.
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=7, ge=1, le=30)
    cookie_secure: bool = True
    login_max_failures: int = Field(default=10, ge=1)
    lockout_minutes: int = Field(default=15, ge=1)
    login_rate_limit_per_minute: int = Field(default=30, ge=1)
    password_reset_minutes: int = Field(default=30, ge=5)

    # E-mail (em desenvolvimento aponta para o scripts/smtp_catcher.py).
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    smtp_from: str = "Dati Monitor <nao-responda@localhost>"

    public_base_url: str = "http://localhost:5173"
    # Endereço que os coletores usam para falar com o servidor (vai no comando de cadastro).
    public_server_url: str = "http://127.0.0.1:8000"
    # WebSocket dos coletores. Vazio = mesmo host do public_server_url em /ws/agent (atrás do Caddy);
    # em desenvolvimento o gateway roda em outra porta (ws://127.0.0.1:8001/ws/agent).
    public_ws_url: str | None = None
    agent_rate_limit_per_minute: int = Field(default=600, ge=10)
    # Comandos remotos (seção 4.7) e arquivos enviados pelos coletores (logs, walks).
    command_expiry_minutes: int = Field(default=10, ge=1, le=1440)
    storage_dir: Path = REPO_ROOT / "var" / "storage"
    gateway_sweep_seconds: int = Field(default=30, ge=1)
    # Equipamento sem leitura há tantas horas aparece como desconectado no parque (seção 8).
    device_disconnected_hours: int = Field(default=6, ge=1, le=720)
    bootstrap_reseller_name: str = "Daticopy"
    bootstrap_admin_email: str = "admin@local"
    # Releases assinadas (seção 5.2): chave PÚBLICA ed25519 em base64. Vazio = a do repositório
    # (agent/internal/release/public.key, a mesma embutida no watchdog). A privada nunca vem para cá.
    release_public_key: str = ""
    # Atualização automática pelo worker; nunca oferece versão com falha acima disto no canary.
    auto_update: bool = True
    update_max_canary_failure_percent: float = Field(default=5.0, ge=0, le=100)

    @field_validator("master_key")
    @classmethod
    def _check_master_key(cls, v: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(v.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("MASTER_KEY precisa estar em base64") from exc
        if len(raw) != 32:  # noqa: PLR2004 - AES-256
            raise ValueError("MASTER_KEY precisa ter 32 bytes (AES-256) codificados em base64")
        return v

    @property
    def master_key_bytes(self) -> bytes:
        return base64.b64decode(self.master_key.get_secret_value())

    @property
    def release_public_key_bytes(self) -> bytes | None:
        """Chave pública das releases (32 bytes), ou None se não houver nenhuma configurada."""
        text = self.release_public_key.strip()
        if not text and RELEASE_PUBLIC_KEY_FILE.exists():
            text = RELEASE_PUBLIC_KEY_FILE.read_text(encoding="utf-8").strip()
        if not text:
            return None
        try:
            raw = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("RELEASE_PUBLIC_KEY precisa estar em base64") from exc
        if len(raw) != 32:  # noqa: PLR2004 - ed25519
            raise ValueError("RELEASE_PUBLIC_KEY precisa ter 32 bytes (ed25519) em base64")
        return raw

    @property
    def agent_ws_url(self) -> str:
        if self.public_ws_url:
            return self.public_ws_url
        u = urlsplit(self.public_server_url)
        scheme = "wss" if u.scheme == "https" else "ws"
        return urlunsplit((scheme, u.netloc, "/ws/agent", "", ""))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
