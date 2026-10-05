import json
import logging
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import version as version_mod
from app.core.config import Settings
from app.core.logging import JsonFormatter, configure_logging
from app.core.product import get_product, load_product
from tests.conftest import PYPROJECT_VERSION


def test_product_from_repo_json() -> None:
    p = get_product()
    assert p.name == "Dati Monitor"
    assert p.agent_service_name == "DatiMonitorAgent"
    assert p.watchdog_service_name == "DatiMonitorWatchdog"


def test_product_rejects_missing_fields(tmp_path: Path) -> None:
    f = tmp_path / "product.json"
    f.write_text(json.dumps({"name": "X"}), encoding="utf-8")
    with pytest.raises(ValueError, match="slug, service_prefix"):
        load_product(f)


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_settings_reject_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "banana")
    with pytest.raises(ValidationError, match="app_env"):
        Settings(database_url="postgresql+asyncpg://x@y/z", _env_file=None)


def test_backend_version_installed() -> None:
    assert version_mod.backend_version() == PYPROJECT_VERSION


def test_backend_version_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: str) -> str:
        raise PackageNotFoundError("dati-monitor-backend")

    monkeypatch.setattr(version_mod, "version", boom)
    with pytest.raises(RuntimeError, match="não instalado"):
        version_mod.backend_version()


def test_json_formatter_includes_exception() -> None:
    try:
        raise ValueError("falhou")
    except ValueError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "msg %s", ("á",), sys.exc_info())
    entry = json.loads(JsonFormatter().format(record))
    assert entry["level"] == "ERROR"
    assert entry["msg"] == "msg á"
    assert "ValueError: falhou" in entry["exc"]


def test_configure_logging_sets_single_json_handler() -> None:
    configure_logging("WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
