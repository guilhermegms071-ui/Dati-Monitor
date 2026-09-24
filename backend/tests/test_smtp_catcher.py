"""Integration test for scripts/smtp_catcher.py: real SMTP delivery and HTTP listing."""

import importlib.util
import json
import smtplib
import socket
import urllib.error
import urllib.request
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.core.product import REPO_ROOT


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smtp_catcher", REPO_ROOT / "scripts" / "smtp_catcher.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture
def catcher(tmp_path: Path) -> Iterator[tuple[Any, int, int]]:
    mod = _load_module()
    smtp_port, http_port = _free_port(), _free_port()
    c = mod.Catcher("127.0.0.1", smtp_port, http_port, tmp_path / "mail")
    c.start()
    try:
        yield c, smtp_port, http_port
    finally:
        c.stop()


def _get(url: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - localhost only
            body = resp.read().decode()
            ctype = resp.headers["Content-Type"]
            return resp.status, json.loads(body) if "json" in ctype else body
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode())


def test_captures_mail_and_lists_it(catcher: tuple[Any, int, int]) -> None:
    c, smtp_port, http_port = catcher
    msg = EmailMessage()
    msg["From"] = "alertas@dati.local"
    msg["To"] = "tecnico@dati.local"
    msg["Subject"] = "Coletor offline: Cliente Á"
    msg.set_content("O coletor PC-01 está sem sinal.")
    with smtplib.SMTP("127.0.0.1", smtp_port, timeout=5) as s:
        s.send_message(msg)

    status, messages = _get(f"http://127.0.0.1:{http_port}/api/messages")
    assert status == 200
    assert len(messages) == 1
    m = messages[0]
    assert m["subject"] == "Coletor offline: Cliente Á"
    assert m["to"] == ["tecnico@dati.local"]
    assert "está sem sinal" in m["text"]
    assert (c.store.directory / f"{m['id']}.eml").exists()

    status, one = _get(f"http://127.0.0.1:{http_port}/api/messages/{m['id']}")
    assert status == 200
    assert one["id"] == m["id"]

    status, page = _get(f"http://127.0.0.1:{http_port}/")
    assert status == 200
    assert "Coletor offline: Cliente Á" in page

    req = urllib.request.Request(f"http://127.0.0.1:{http_port}/api/messages", method="DELETE")
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
        assert json.loads(resp.read())["deleted"] == 1
    assert _get(f"http://127.0.0.1:{http_port}/api/messages")[1] == []


def test_unknown_routes_and_ids(catcher: tuple[Any, int, int]) -> None:
    _, _, http_port = catcher
    assert _get(f"http://127.0.0.1:{http_port}/nada")[0] == 404
    assert _get(f"http://127.0.0.1:{http_port}/api/messages/../../etc")[0] == 404
    assert _get(f"http://127.0.0.1:{http_port}/api/messages/20260101T000000Z-deadbeef")[0] == 404
