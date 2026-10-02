"""Página web de impressora simulada (E2E e desenvolvimento do acesso remoto, PROMPT 4.9).

Imita o que as páginas reais fazem e o túnel precisa reescrever: redirecionamento com URL absoluta do
próprio IP, cookie de sessão com Path=/, links e CSS com caminho a partir da raiz e uma página que só
abre com o cookie. Só biblioteca padrão.

Uso:  .venv\\Scripts\\python scripts\\printer_web_sim.py --port 8080
"""

from __future__ import annotations

import argparse
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("printer_web_sim")

INDEX = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>bizhub C287</title>
<link rel="stylesheet" href="/web/style.css"></head><body>
<h1>Konica Minolta bizhub C287 — página de teste</h1>
<p><a href="/web/status.html">Contadores</a> · <a href="http://{host}/web/index.html">Início</a></p>
</body></html>"""
STATUS_OK = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Contadores</title></head>
<body><h1>Contadores</h1><p id="total">Contador total: 217.031</p>
<p>PB: 100.150 · Cor: 116.881</p></body></html>"""
STATUS_DENIED = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Login</title></head>
<body><p>Sessão da impressora ausente: faça login.</p></body></html>"""
CSS = "body{font-family:sans-serif}h1{color:#0b5394}"


class Handler(BaseHTTPRequestHandler):
    server_version = "PrinterSim/1.0"

    def _send(self, status: int, body: str, ctype: str, headers: dict[str, str] | None = None) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def do_GET(self) -> None:
        host = self.headers.get("Host", "127.0.0.1")
        match self.path.split("?")[0]:
            case "/":
                self._send(302, "", "text/plain", {"Location": f"http://{host}/web/index.html"})
            case "/health":
                self._send(200, "ok", "text/plain")
            case "/web/index.html":
                cookie = {"Set-Cookie": "session=konica; Path=/; HttpOnly"}
                self._send(200, INDEX.format(host=host), "text/html; charset=utf-8", cookie)
            case "/web/style.css":
                self._send(200, CSS, "text/css")
            case "/web/status.html":
                ok = "session=konica" in self.headers.get("Cookie", "")
                self._send(200 if ok else 401, STATUS_OK if ok else STATUS_DENIED, "text/html; charset=utf-8")
            case _:
                self._send(404, "não encontrado", "text/plain; charset=utf-8")

    def do_HEAD(self) -> None:
        self.do_GET()

    def log_message(self, fmt: str, *args: object) -> None:
        log.info("%s %s", self.address_string(), fmt % args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    logging.basicConfig(level=logging.INFO, format="[printer_web_sim] %(message)s")
    log.info("página da impressora simulada em http://%s:%d/", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
