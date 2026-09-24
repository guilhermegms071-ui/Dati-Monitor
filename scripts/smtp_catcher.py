"""Captura de e-mails para desenvolvimento/testes (substitui o Mailpit, sem Docker).

- SMTP em 127.0.0.1:1025: cada mensagem é gravada em var/mail/<id>.eml e <id>.json.
- HTTP em 127.0.0.1:8025: "/" lista as mensagens (HTML), "/api/messages" em JSON,
  "/api/messages/<id>" retorna uma mensagem e DELETE "/api/messages" apaga todas.

Uso: .venv/Scripts/python scripts/smtp_catcher.py [--smtp-port 1025] [--http-port 8025] [--mail-dir var/mail]
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import signal
import sys
import threading
import uuid
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP, Envelope, Session

logger = logging.getLogger("smtp_catcher")
REPO_ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


class MailStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def save(self, mail_from: str, rcpt_tos: list[str], raw: bytes) -> dict[str, Any]:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        received = datetime.now(UTC)
        msg_id = f"{received:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        body = msg.get_body(preferencelist=("plain", "html"))
        meta = {
            "id": msg_id,
            "received_at": received.isoformat(),
            "from": mail_from,
            "to": rcpt_tos,
            "subject": str(msg.get("Subject", "")),
            "text": body.get_content() if body is not None else "",
        }
        with self._lock:
            (self.directory / f"{msg_id}.eml").write_bytes(raw)
            (self.directory / f"{msg_id}.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )
        logger.info("e-mail recebido: %s -> %s: %s", mail_from, ", ".join(rcpt_tos), meta["subject"])
        return meta

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            files = sorted(self.directory.glob("*.json"), reverse=True)
            return [json.loads(f.read_text(encoding="utf-8")) for f in files]

    def get(self, msg_id: str) -> dict[str, Any] | None:
        if not ID_RE.match(msg_id):
            return None
        path = self.directory / f"{msg_id}.json"
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def clear(self) -> int:
        with self._lock:
            removed = 0
            for f in self.directory.glob("*.json"):
                f.unlink()
                removed += 1
            for f in self.directory.glob("*.eml"):
                f.unlink()
            return removed


class SmtpHandler:
    def __init__(self, store: MailStore) -> None:
        self.store = store

    async def handle_DATA(self, server: SMTP, session: Session, envelope: Envelope) -> str:  # noqa: N802
        content = envelope.original_content or envelope.content
        raw = content if isinstance(content, bytes) else str(content).encode()
        try:
            self.store.save(str(envelope.mail_from), [str(r) for r in envelope.rcpt_tos], raw)
        except Exception:
            logger.exception("falha ao gravar e-mail")
            return "451 Falha ao gravar a mensagem"
        return "250 OK"


def make_http_handler(store: MailStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(
                status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode()
            )

        def do_GET(self) -> None:
            if self.path == "/api/messages":
                self._json(200, store.list())
            elif self.path.startswith("/api/messages/"):
                msg = store.get(self.path.rsplit("/", 1)[-1])
                if msg is None:
                    self._json(404, {"error": "mensagem não encontrada"})
                else:
                    self._json(200, msg)
            elif self.path == "/":
                rows = "".join(
                    f"<tr><td>{html.escape(m['received_at'])}</td><td>{html.escape(m['from'])}</td>"
                    f"<td>{html.escape(', '.join(m['to']))}</td><td>{html.escape(m['subject'])}</td>"
                    f"<td><pre>{html.escape(m['text'])}</pre></td></tr>"
                    for m in store.list()
                )
                page = (
                    "<!doctype html><html lang='pt-BR'><meta charset='utf-8'>"
                    "<title>E-mails capturados</title>"
                    "<style>body{font-family:sans-serif}td,th{border:1px solid #ccc;padding:4px;"
                    "vertical-align:top}pre{white-space:pre-wrap;margin:0}</style>"
                    "<h1>E-mails capturados (desenvolvimento)</h1><table><tr><th>Recebido (UTC)</th>"
                    f"<th>De</th><th>Para</th><th>Assunto</th><th>Texto</th></tr>{rows}</table>"
                )
                self._send(200, "text/html; charset=utf-8", page.encode())
            else:
                self._json(404, {"error": "rota não encontrada"})

        def do_DELETE(self) -> None:
            if self.path == "/api/messages":
                self._json(200, {"deleted": store.clear()})
            else:
                self._json(404, {"error": "rota não encontrada"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.debug("http: " + format, *args)

    return Handler


class Catcher:
    def __init__(self, host: str, smtp_port: int, http_port: int, mail_dir: Path) -> None:
        self.store = MailStore(mail_dir)
        self.smtp = Controller(SmtpHandler(self.store), hostname=host, port=smtp_port)
        self.http = ThreadingHTTPServer((host, http_port), make_http_handler(self.store))
        self._http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def start(self) -> None:
        self.smtp.start()
        self._http_thread.start()

    def stop(self) -> None:
        self.http.shutdown()
        self.http.server_close()
        self.smtp.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--smtp-port", type=int, default=1025)
    parser.add_argument("--http-port", type=int, default=8025)
    parser.add_argument("--mail-dir", type=Path, default=REPO_ROOT / "var" / "mail")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    catcher = Catcher(args.host, args.smtp_port, args.http_port, args.mail_dir)
    catcher.start()
    logger.info("SMTP em %s:%d, lista em http://%s:%d", args.host, args.smtp_port, args.host, args.http_port)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.wait(0.5):
            pass
    finally:
        catcher.stop()
        logger.info("smtp_catcher encerrado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
