"""Settings from environment variables (and the repository .env in development)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.product import REPO_ROOT


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
