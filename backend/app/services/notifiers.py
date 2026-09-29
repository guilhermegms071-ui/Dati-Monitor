"""Notifiers (PROMPT 9): one interface, three transports — SMTP, generic webhook and WhatsApp (Meta Cloud
API or a generic HTTP provider such as Z-API/Evolution API through URL/body templates).

Every failure raises NotifyError with a message in Portuguese; the delivery job records it in
`notifications.error`, retries with backoff and logs it (errors are never silent).
"""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import quote

import aiosmtplib
import httpx

from app.core.config import Settings

HTTP_TIMEOUT = 15.0


class NotifyError(Exception):
    """Falha de envio; a mensagem vai para o registro da notificação."""


@dataclass(frozen=True)
class Message:
    subject: str
    body: str
    payload: dict[str, Any]  # dados estruturados (webhook): alerta, equipamento, coletor…


class Notifier(Protocol):
    async def send(self, destination: str, message: Message) -> None: ...


def _http_error(resp: httpx.Response) -> NotifyError:
    text = resp.text[:300].replace("\n", " ")
    return NotifyError(f"o destino respondeu HTTP {resp.status_code}: {text}")


class SmtpNotifier:
    """E-mail pelo SMTP do canal ou, sem configuração própria, pelo SMTP do servidor."""

    def __init__(self, settings: Settings, config: dict[str, Any]) -> None:
        self.host = config.get("smtp_host") or settings.smtp_host
        self.port = int(config.get("smtp_port") or settings.smtp_port)
        self.username = config.get("username") or settings.smtp_username
        password = config.get("password")
        self.password = password or (
            settings.smtp_password.get_secret_value() if settings.smtp_password else None
        )
        self.starttls = bool(config.get("starttls", settings.smtp_starttls))
        self.sender = config.get("from") or settings.smtp_from

    async def send(self, destination: str, message: Message) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = destination
        msg["Subject"] = message.subject
        msg.set_content(message.body)
        try:
            await aiosmtplib.send(
                msg,
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                start_tls=self.starttls,
                timeout=HTTP_TIMEOUT,
            )
        except (aiosmtplib.SMTPException, OSError) as exc:
            raise NotifyError(f"falha no SMTP {self.host}:{self.port}: {exc}") from exc


class WebhookNotifier:
    """POST JSON para a URL do canal; com `secret`, assina o corpo (X-Dati-Signature: sha256=<hmac>)."""

    def __init__(self, config: dict[str, Any], client: httpx.AsyncClient) -> None:
        self.secret = str(config.get("secret") or "")
        self.headers = {str(k): str(v) for k, v in (config.get("headers") or {}).items()}
        self.client = client

    async def send(self, destination: str, message: Message) -> None:
        body = json.dumps(
            {"subject": message.subject, "text": message.body, **message.payload}, ensure_ascii=False
        ).encode()
        headers = {"Content-Type": "application/json", **self.headers}
        if self.secret:
            sig = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Dati-Signature"] = f"sha256={sig}"
        try:
            resp = await self.client.post(destination, content=body, headers=headers, timeout=HTTP_TIMEOUT)
        except httpx.HTTPError as exc:
            raise NotifyError(f"falha ao chamar o webhook: {exc}") from exc
        if resp.is_error:
            raise _http_error(resp)


def normalize_phone(phone: str) -> str:
    """Número internacional só com dígitos (55 21 99999-0000 → 5521999990000)."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:  # noqa: PLR2004 - DDD + número
        raise NotifyError(f"telefone inválido para WhatsApp: {phone}")
    return digits


class MetaWhatsAppNotifier:
    """Meta WhatsApp Cloud API: POST {base}/{phone_number_id}/messages com o texto da mensagem."""

    def __init__(self, settings: Settings, config: dict[str, Any], client: httpx.AsyncClient) -> None:
        self.base = str(config.get("api_base") or settings.whatsapp_meta_api_base).rstrip("/")
        self.phone_number_id = str(config.get("phone_number_id") or "")
        self.token = str(config.get("access_token") or "")
        if not self.phone_number_id or not self.token:
            raise NotifyError("canal WhatsApp (Meta) sem phone_number_id ou access_token")
        self.client = client

    async def send(self, destination: str, message: Message) -> None:
        body = {
            "messaging_product": "whatsapp",
            "to": normalize_phone(destination),
            "type": "text",
            "text": {"preview_url": False, "body": f"*{message.subject}*\n{message.body}"[:4096]},
        }
        try:
            resp = await self.client.post(
                f"{self.base}/{self.phone_number_id}/messages",
                json=body,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"falha ao chamar a API do WhatsApp: {exc}") from exc
        if resp.is_error:
            raise _http_error(resp)


def _render(template: str, values: dict[str, str], *, url: bool) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", quote(value, safe="") if url else value)
    return out


class GenericWhatsAppNotifier:
    """Provedor HTTP genérico (Z-API, Evolution API…): URL e corpo por modelo com {phone}, {subject},
    {message} e {text} (assunto + mensagem). O corpo é JSON: os valores são escapados como string JSON."""

    def __init__(self, config: dict[str, Any], client: httpx.AsyncClient) -> None:
        self.url_template = str(config.get("url_template") or "")
        if not self.url_template.startswith(("http://", "https://")):
            raise NotifyError("canal WhatsApp (genérico) sem url_template http(s)")
        self.method = str(config.get("method") or "POST").upper()
        self.body_template = config.get("body_template")
        self.headers = {str(k): str(v) for k, v in (config.get("headers") or {}).items()}
        self.client = client

    async def send(self, destination: str, message: Message) -> None:
        values = {
            "phone": normalize_phone(destination),
            "subject": message.subject,
            "message": message.body,
            "text": f"{message.subject}\n{message.body}",
        }
        url = _render(self.url_template, values, url=True)
        content: bytes | None = None
        headers = dict(self.headers)
        if self.body_template:
            escaped = {k: json.dumps(v, ensure_ascii=False)[1:-1] for k, v in values.items()}
            rendered = _render(str(self.body_template), escaped, url=False)
            try:
                json.loads(rendered)
            except json.JSONDecodeError as exc:
                raise NotifyError(f"body_template não gera JSON válido: {exc}") from exc
            content = rendered.encode()
            headers.setdefault("Content-Type", "application/json")
        try:
            resp = await self.client.request(
                self.method, url, content=content, headers=headers, timeout=HTTP_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"falha ao chamar o provedor de WhatsApp: {exc}") from exc
        if resp.is_error:
            raise _http_error(resp)


def build_notifier(
    kind: str, config: dict[str, Any], settings: Settings, client: httpx.AsyncClient
) -> Notifier:
    if kind == "email":
        return SmtpNotifier(settings, config)
    if kind == "webhook":
        return WebhookNotifier(config, client)
    if kind == "whatsapp":
        provider = config.get("provider", "meta")
        if provider == "meta":
            return MetaWhatsAppNotifier(settings, config, client)
        if provider == "generic":
            return GenericWhatsAppNotifier(config, client)
        raise NotifyError(f"provedor de WhatsApp desconhecido: {provider}")
    raise NotifyError(f"tipo de canal desconhecido: {kind}")
