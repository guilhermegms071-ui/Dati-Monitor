"""Input validators shared by schemas: CNPJ, e-mail, passwords, IANA time zones."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
MIN_PASSWORD_LENGTH = 10
CNPJ_LENGTH = 14


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 320 or not _EMAIL_RE.match(email):  # noqa: PLR2004 - RFC 5321
        raise ValueError("e-mail inválido")
    return email


def normalize_cnpj(value: str | None) -> str | None:
    """Accepts formatted or digits-only CNPJ; returns 14 digits or raises on invalid check digits."""
    if value is None or not value.strip():
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) != CNPJ_LENGTH or digits == digits[0] * CNPJ_LENGTH:
        raise ValueError("CNPJ inválido")
    for size in (12, 13):
        weights = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][13 - size :]
        total = sum(int(d) * w for d, w in zip(digits[:size], weights, strict=True))
        check = 0 if total % 11 < 2 else 11 - total % 11  # noqa: PLR2004
        if int(digits[size]) != check:
            raise ValueError("CNPJ inválido (dígito verificador)")
    return digits


def check_password_policy(password: str, email: str | None = None) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"a senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres")
    if len(password) > 256:  # noqa: PLR2004
        raise ValueError("a senha pode ter no máximo 256 caracteres")
    if email and password.strip().lower() == email.strip().lower():
        raise ValueError("a senha não pode ser igual ao e-mail")
    if len(set(password)) < 4:  # noqa: PLR2004
        raise ValueError("a senha é fraca demais (poucos caracteres distintos)")
    return password


def check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"fuso horário desconhecido: {value}") from exc
    return value
