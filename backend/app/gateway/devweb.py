"""/devweb/{token}/...: the printer's web page through the collector's WebSocket (PROMPT 4.9).

Each browser request becomes a `web_request` frame to the collector that holds the session; the answer
comes back as `web_response` + `web_chunk` frames (multiplexed by `stream_id`). HTML/CSS/JS are rewritten so
links, redirects and cookies stay under /devweb/{token}/.

Isolation: the printer page is served with `Content-Security-Policy: sandbox` WITHOUT `allow-same-origin`,
so its scripts run in an opaque origin — they cannot read the portal's cookies/storage nor call the API as
the user. The printer's own cookies are rewritten to `SameSite=None; Secure` and scoped to the session
path, so they keep working inside the sandbox.
"""

import asyncio
import base64
import binascii
import html
import logging
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import ValidationError

from app.core.config import Settings
from app.gateway.hub import Connection, Hub, ws_message
from app.schemas import agent as proto
from app.services import web_access as svc

logger = logging.getLogger(__name__)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
# Cabeçalhos da impressora que não podem passar (quebram o isolamento ou a reescrita).
DROP_RESPONSE = HOP_BY_HOP | {
    "content-security-policy",
    "content-security-policy-report-only",
    "x-frame-options",
    "strict-transport-security",
    "content-encoding",
    "set-cookie",
    "location",
    "refresh",
}
REWRITE_TYPES = ("text/html", "text/css", "javascript", "application/xhtml", "text/xml", "application/xml")
MAX_REQUEST_BODY = 10 * 1024 * 1024
MAX_REWRITE_BODY = 8 * 1024 * 1024
COMMAND_WAIT_SECONDS = 20
SANDBOX_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads"
# `/(?!/|PREFIXO)`: caminho absoluto no servidor da impressora que ainda não está sob o prefixo.
ATTR_PATTERN = (
    r"""(?i)(\b(?:href|src|action|formaction|background|poster|data|codebase|longdesc|cite)\s*=\s*)(["']?)/"""
)
CSS_URL_PATTERN = r"""(?i)(url\(\s*)(["']?)/"""
CSS_IMPORT_PATTERN = r"""(?i)(@import\s+)(["'])/"""
META_REFRESH_PATTERN = r"""(?i)(content\s*=\s*["']?\s*\d+\s*;\s*url\s*=\s*)(["']?)/"""
# Caminho absoluto entre aspas dentro de JavaScript ("/wcd/index.html", '/login'): troca de página por
# location.href/replace, que o script injetado não consegue interceptar. Só texto com cara de caminho
# (segue /, ., ?, # ou fecha a aspa), para não mexer em expressões regulares como /'/g.
JS_PATH_PATTERN = r"""()(["'])/(?=[A-Za-z0-9_-]+(?:[/.?#]|\2))"""
HEAD_RE = re.compile(r"(?i)<head[^>]*>")


# ----------------------------------------------------------------------------- reescrita


def origins(ip: str, port: int, scheme: str) -> list[str]:
    """Every way the printer may write its own absolute URL (longest first)."""
    default = 443 if scheme == "https" else 80
    hosts = [f"{ip}:{port}"] + ([ip] if port == default else [])
    out = []
    for h in hosts:
        out += [f"{scheme}://{h}", f"//{h}"]
        other = "http" if scheme == "https" else "https"
        out.append(f"{other}://{h}")
    return sorted(set(out), key=len, reverse=True)


def rewrite_url(value: str, prefix: str, device_origins: list[str]) -> str:
    """Absolute URL of the printer or root-relative path → the same path under the session prefix."""
    v = value.strip()
    for o in device_origins:
        if v == o:
            return prefix
        if v.startswith(o + "/"):
            return prefix + v[len(o) + 1 :]
        if v.startswith(o + "?"):
            return prefix + v[len(o) :]
    if v.startswith("/") and not v.startswith("//") and not v.startswith(prefix):
        return prefix + v[1:]
    return v


def rewrite_set_cookie(value: str, prefix: str) -> str:
    """Scopes the printer's cookie to the session path and makes it work inside the sandbox."""
    parts = [p.strip() for p in value.split(";")]
    head, attrs = parts[0], parts[1:]
    kept = []
    path = prefix
    for a in attrs:
        name = a.split("=", 1)[0].strip().lower()
        if name == "path":
            raw = a.split("=", 1)[1].strip() if "=" in a else "/"
            path = prefix + raw.lstrip("/") if raw.startswith("/") else prefix
        elif name in ("domain", "samesite", "secure"):
            continue
        elif a:
            kept.append(a)
    return "; ".join([head, *kept, f"Path={path}", "SameSite=None", "Secure"])


def _inject(prefix: str) -> str:
    p = html.escape(prefix, quote=True)
    return (
        "<script>(function(){var P='" + p + "';function f(u){try{if(typeof u==='string'&&u.charAt(0)==='/'"
        "&&u.charAt(1)!=='/'&&u.indexOf(P)!==0){return P+u.slice(1);}}catch(e){}return u;}"
        "var o=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){arguments[1]=f(u);"
        "return o.apply(this,arguments);};if(window.fetch){var F=window.fetch;window.fetch=function(u,i){"
        "return F.call(this,typeof u==='string'?f(u):u,i);};}var W=window.open;window.open=function(u){"
        "arguments[0]=f(u);return W.apply(this,arguments);};})();</script>"
    )


def _sub(pattern: str, prefix: str, text: str) -> str:
    rx = re.compile(pattern + f"(?!/|{re.escape(prefix[1:])})")
    return rx.sub(lambda m: m.group(1) + m.group(2) + prefix, text)


def rewrite_body(text: str, content_type: str, prefix: str, device_origins: list[str]) -> str:
    for o in device_origins:
        text = text.replace(o + "/", prefix)
    if "html" in content_type or "xml" in content_type:
        text = _sub(ATTR_PATTERN, prefix, text)
        text = _sub(META_REFRESH_PATTERN, prefix, text)
        text = _sub(CSS_URL_PATTERN, prefix, text)
        text = _sub(JS_PATH_PATTERN, prefix, text)
        if "html" in content_type:
            script = _inject(prefix)
            text, n = HEAD_RE.subn(lambda m: m.group(0) + script, text, count=1)
            if not n:
                text = script + text
    elif "javascript" in content_type or "ecmascript" in content_type:
        text = _sub(JS_PATH_PATTERN, prefix, text)
    elif "css" in content_type:
        text = _sub(CSS_URL_PATTERN, prefix, text)
        text = _sub(CSS_IMPORT_PATTERN, prefix, text)
    return text


def _charset(content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    return m.group(1) if m else "utf-8"


# ----------------------------------------------------------------------------- multiplexação


@dataclass
class Stream:
    agent_id: uuid.UUID
    queue: asyncio.Queue[tuple[str, object]] = field(default_factory=asyncio.Queue)


class WebTunnel:
    """Streams in flight of this gateway process (stream_id → queue of frames)."""

    def __init__(self) -> None:
        self._streams: dict[str, Stream] = {}

    def open(self, agent_id: uuid.UUID) -> tuple[str, Stream]:
        sid = uuid.uuid4().hex
        stream = Stream(agent_id)
        self._streams[sid] = stream
        return sid, stream

    def close(self, stream_id: str) -> None:
        self._streams.pop(stream_id, None)

    def on_frame(self, agent_id: uuid.UUID, kind: str, data: dict[str, object]) -> str | None:
        """Frame from a collector. Returns an error text when the frame is invalid."""
        try:
            match kind:
                case "web_response":
                    msg: proto.WebResponseStart | proto.WebChunk | proto.WebError = (
                        proto.WebResponseStart.model_validate(data)
                    )
                case "web_chunk":
                    msg = proto.WebChunk.model_validate(data)
                case _:
                    msg = proto.WebError.model_validate(data)
        except ValidationError:
            return f"{kind} inválido"
        stream = self._streams.get(msg.stream_id)
        if stream is None or stream.agent_id != agent_id:
            return None  # o navegador já desistiu (ou é de outro coletor): descarta em silêncio de propósito
        stream.queue.put_nowait((kind, msg))
        return None

    def drop_agent(self, agent_id: uuid.UUID) -> None:
        for s in self._streams.values():
            if s.agent_id == agent_id:
                s.queue.put_nowait(
                    ("web_error", proto.WebError(stream_id="", message="o coletor desconectou"))
                )

    def active(self) -> int:
        return len(self._streams)


# ----------------------------------------------------------------------------- rota HTTP


def error_page(status: int, message: str) -> HTMLResponse:
    body = (
        "<!doctype html><html lang='pt-BR'><meta charset='utf-8'><title>Acesso à impressora</title>"
        "<body style='font-family:system-ui;margin:3rem;color:#0f172a'><h1 style='font-size:1.3rem'>"
        "Não foi possível abrir a página da impressora</h1>"
        f"<p role='alert' style='color:#b91c1c'>{html.escape(message)}</p>"
        "<p style='color:#475569'>Feche esta aba e abra de novo pelo portal (Equipamento → Abrir página web)."
        "</p></body></html>"
    )
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _forward_headers(request: Request, device_origin: str, prefix_url: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in HOP_BY_HOP or lk in ("accept-encoding", "origin", "referer", "cookie"):
            continue
        out.append((k, v))
    cookies = [
        c.strip()
        for c in request.headers.get("cookie", "").split(";")
        if c.strip() and not c.strip().startswith(svc.COOKIE + "=")
    ]
    if cookies:
        out.append(("Cookie", "; ".join(cookies)))
    out.append(("Accept-Encoding", "identity"))
    referer = request.headers.get("referer", "")
    if referer.startswith(prefix_url):
        out.append(("Referer", device_origin + "/" + referer[len(prefix_url) :]))
    if request.method not in ("GET", "HEAD"):
        out.append(("Origin", device_origin))
    return out


async def _wait_open(hub: Hub, command_id: uuid.UUID | None) -> str | None:
    """Waits the collector to confirm `web_proxy_open`. Returns an error text, or None when ready."""
    if command_id is None:
        return "sessão sem comando de abertura"
    from app.models import Command  # noqa: PLC0415

    for _ in range(COMMAND_WAIT_SECONDS * 4):
        async with hub.sessionmaker() as session:
            cmd = await session.get(Command, command_id)
            state = cmd.state if cmd else None
            error = (cmd.result or {}).get("error") if cmd else None
        if state == "succeeded":
            return None
        if state in ("failed", "expired", "cancelled") or state is None:
            return f"o coletor não abriu o acesso ({error or state})"
        await asyncio.sleep(0.25)
    return "o coletor não confirmou a abertura a tempo; ele está online?"


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _insecure_origin(request: Request) -> bool:
    """Navegador em http fora do localhost: o cookie Secure da sessão não é guardado."""
    if request.headers.get("x-forwarded-proto", request.url.scheme).lower() == "https":
        return False
    host = request.headers.get("host", "")
    hostname = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
    return hostname.lower() not in LOCAL_HOSTS


async def handle(request: Request, token: str, path: str, settings: Settings) -> Response:
    hub: Hub = request.app.state.hub
    tunnel: WebTunnel = request.app.state.tunnel
    prefix = svc.path_prefix(token)
    key = request.query_params.get(svc.KEY_PARAM)
    async with hub.sessionmaker() as session:
        try:
            ws, active = await svc.resolve(session, token)
            if key is not None:
                if ws.bound_at is None:
                    await svc.bind(session, ws, key)
                elif not svc.check_key(ws, request.cookies.get(svc.COOKIE, "")):
                    # Link já usado: só o navegador que o abriu primeiro (com o cookie) continua.
                    raise svc.SessionRefused(
                        403, "Este link já foi aberto em outro navegador; abra uma nova sessão pelo portal"
                    )
                query = "&".join(
                    f"{k}={v}" for k, v in request.query_params.multi_items() if k != svc.KEY_PARAM
                )
                target = prefix + path + (f"?{query}" if query else "")
                resp: Response = RedirectResponse(target, status_code=303)
                resp.set_cookie(
                    svc.COOKIE, key, path=prefix, httponly=True, secure=True, samesite="none",
                    max_age=settings.web_session_minutes * 60,
                )  # fmt: skip
                return resp
            cookie = request.cookies.get(svc.COOKIE)
            if not cookie and _insecure_origin(request):
                # O cookie é Secure (precisa ser, por causa do sandbox): em http fora do localhost
                # o navegador o descarta e a sessão nunca chega aqui com ele.
                raise svc.SessionRefused(
                    403,
                    "O navegador não guardou o cookie de segurança: a página da impressora só abre com o "
                    "portal em https ou pelo endereço localhost do servidor (ex.: http://localhost:5173 "
                    "no próprio PC do servidor). Na hospedagem com https funciona de qualquer computador.",
                )
            if not cookie or not svc.check_key(ws, cookie):
                raise svc.SessionRefused(
                    403, "Esta sessão pertence a outro navegador; abra de novo pelo portal"
                )
        except svc.SessionRefused as exc:
            return error_page(exc.status, exc.message)
    if active.command_state != "succeeded":
        problem = await _wait_open(hub, ws.command_id)
        if problem:
            return error_page(502, problem)
    if active.bytes_out >= settings.web_max_bytes_per_session:
        return error_page(429, "Limite de dados desta sessão atingido; abra uma nova sessão")
    conn = hub.get(active.agent_id)
    if conn is None:
        return error_page(
            503,
            "O coletor desta sessão não está conectado a este servidor agora (ele caiu ou reconectou "
            "em outra instância do gateway); tente de novo em instantes",
        )
    body = await request.body()
    if len(body) > MAX_REQUEST_BODY:
        return error_page(413, "Envio grande demais para a página da impressora (máx. 10 MB)")
    default_port = 443 if active.scheme == "https" else 80
    host = active.ip if active.port == default_port else f"{active.ip}:{active.port}"
    device_origin = f"{active.scheme}://{host}"
    prefix_url = str(request.base_url).rstrip("/") + prefix
    query = request.url.query
    req = proto.WebRequest(
        stream_id="",
        session_id=str(active.id),
        method=request.method,
        path="/" + path + (f"?{query}" if query else ""),
        headers=_forward_headers(request, device_origin, prefix_url),
        body_b64=base64.b64encode(body).decode() if body else None,
    )
    return await ProxyCall(hub, tunnel, conn, active, prefix, settings).run(req)


def response_headers(
    msg: proto.WebResponseStart, prefix: str, dev_origins: list[str]
) -> tuple[list[tuple[str, str]], str]:
    """Printer headers → browser headers (redirects and cookies under the prefix, isolation added)."""
    headers: list[tuple[str, str]] = []
    content_type = ""
    for k, v in msg.headers:
        lk = k.lower()
        if lk == "content-type":
            content_type = v
        if lk == "location":
            headers.append(("Location", rewrite_url(v, prefix, dev_origins)))
        elif lk == "refresh":
            m = re.match(r"(?i)(.*?url=)(.*)", v)
            headers.append(("Refresh", m.group(1) + rewrite_url(m.group(2), prefix, dev_origins) if m else v))
        elif lk == "set-cookie":
            headers.append(("Set-Cookie", rewrite_set_cookie(v, prefix)))
        elif lk not in DROP_RESPONSE:
            headers.append((k, v))
    headers += [
        ("Content-Security-Policy", SANDBOX_CSP),
        ("X-Frame-Options", "SAMEORIGIN"),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
    ]
    return headers, content_type


@dataclass
class ProxyCall:
    hub: Hub
    tunnel: WebTunnel
    conn: Connection
    active: svc.ActiveSession
    prefix: str
    settings: Settings

    async def next_frame(self, stream: Stream) -> tuple[str, object]:
        return await asyncio.wait_for(stream.queue.get(), self.settings.web_response_timeout_seconds)

    async def body(self, sid: str, stream: Stream) -> AsyncIterator[bytes]:
        """Body chunks of the printer's answer; accounts the bytes and enforces the session limit."""
        total = 0
        limit = self.settings.web_max_bytes_per_session - self.active.bytes_out
        try:
            while True:
                kind, frame = await self.next_frame(stream)
                if not isinstance(frame, proto.WebChunk):
                    logger.warning("túnel web %s: %s", self.active.id, getattr(frame, "message", kind))
                    return
                if frame.data_b64:
                    try:
                        data = base64.b64decode(frame.data_b64)
                    except binascii.Error:
                        logger.error("túnel web %s: pedaço em base64 inválido", self.active.id)
                        return
                    total += len(data)
                    if total > limit:
                        logger.warning("túnel web %s: limite de dados da sessão atingido", self.active.id)
                        return
                    yield data
                if frame.end:
                    return
        finally:
            self.tunnel.close(sid)
            async with self.hub.sessionmaker() as session:
                await svc.account(session, self.active.id, total)

    async def run(self, req: proto.WebRequest) -> Response:
        sid, stream = self.tunnel.open(self.active.agent_id)
        req.stream_id = sid
        try:
            await self.conn.send(ws_message("web_request", req.model_dump(mode="json")))
            kind, msg = await self.next_frame(stream)
        except (TimeoutError, OSError, RuntimeError) as exc:
            self.tunnel.close(sid)
            return error_page(504, f"A impressora não respondeu a tempo ({exc.__class__.__name__})")
        if not isinstance(msg, proto.WebResponseStart):
            self.tunnel.close(sid)
            text = msg.message if isinstance(msg, proto.WebError) else f"resposta inesperada ({kind})"
            return error_page(502, f"O coletor não conseguiu abrir a página: {text}")
        dev_origins = origins(self.active.ip, self.active.port, self.active.scheme)
        headers, content_type = response_headers(msg, self.prefix, dev_origins)
        if not any(t in content_type.lower() for t in REWRITE_TYPES):
            out: Response = StreamingResponse(self.body(sid, stream), status_code=msg.status)
        else:
            buf = bytearray()
            async for part in self.body(sid, stream):
                buf += part
                if len(buf) > MAX_REWRITE_BODY:
                    return error_page(502, "Página grande demais para abrir pelo túnel")
            charset = _charset(content_type)
            text = rewrite_body(buf.decode(charset, errors="replace"), content_type, self.prefix, dev_origins)
            out = Response(content=text.encode(charset, errors="replace"), status_code=msg.status)
        for k, v in headers:
            out.headers.append(k, v)
        return out
