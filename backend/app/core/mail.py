"""Outgoing e-mail over SMTP (development: scripts/smtp_catcher.py on 127.0.0.1:1025)."""

import logging
from email.message import EmailMessage

import aiosmtplib

from app.core.config import Settings

logger = logging.getLogger(__name__)


class MailError(Exception):
    pass


async def send_email(settings: Settings, *, to: list[str], subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            start_tls=settings.smtp_starttls,
            timeout=15,
        )
    except (aiosmtplib.SMTPException, OSError) as exc:
        logger.error("falha ao enviar e-mail para %s: %s", ", ".join(to), exc)
        raise MailError(f"falha ao enviar e-mail: {exc}") from exc
