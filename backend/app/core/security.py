"""Password hashing (argon2id), JWT access tokens and opaque token helpers."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id com os parâmetros recomendados (RFC 9106, perfil de baixa memória)
# Hash de uma senha aleatória para igualar o tempo de resposta quando o usuário não existe.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))

JWT_ALGORITHM = "HS256"
LimitedReason = Literal["password_change_required", "totp_setup_required"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-effort verification; returns False for unknown users too (dummy hash)."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError, UnicodeError, ValueError):
        # Hash corrompido/ilegível no banco: trata como senha incorreta (nunca como sucesso).
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    reseller_id: uuid.UUID
    role: str
    customer_id: uuid.UUID | None
    limited: LimitedReason | None
    issued_at: datetime
    expires_at: datetime
    token_id: str
    password_version: int | None


def create_access_token(
    *,
    secret: str,
    user_id: uuid.UUID,
    reseller_id: uuid.UUID,
    role: str,
    customer_id: uuid.UUID | None,
    minutes: int,
    limited: LimitedReason | None = None,
    password_changed_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued = (now or datetime.now(UTC)).replace(microsecond=0)
    expires = issued + timedelta(minutes=minutes)
    payload: dict[str, Any] = {
        "typ": "access",
        "sub": str(user_id),
        "rid": str(reseller_id),
        "role": role,
        "cid": str(customer_id) if customer_id else None,
        "lim": limited,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_hex(8),
        # Versão da senha: trocar a senha invalida na hora todos os tokens emitidos antes.
        "pwv": password_version(password_changed_at),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM), expires


def password_version(changed_at: datetime | None) -> int | None:
    return int(changed_at.timestamp() * 1_000_000) if changed_at else None


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True)
class AgentClaims:
    agent_id: uuid.UUID
    reseller_id: uuid.UUID
    site_id: uuid.UUID
    expires_at: datetime


def create_agent_token(
    *, secret: str, agent_id: uuid.UUID, reseller_id: uuid.UUID, site_id: uuid.UUID, minutes: int = 15
) -> tuple[str, datetime]:
    issued = datetime.now(UTC).replace(microsecond=0)
    expires = issued + timedelta(minutes=minutes)
    payload = {
        "typ": "agent",
        "sub": str(agent_id),
        "rid": str(reseller_id),
        "sid": str(site_id),
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM), expires


def decode_agent_token(token: str, *, secret: str) -> AgentClaims:
    try:
        data = jwt.decode(
            token, secret, algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub", "typ"]}
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("token expirado") from exc
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("token inválido") from exc
    if data.get("typ") != "agent":
        raise InvalidTokenError("tipo de token inválido")
    try:
        return AgentClaims(
            agent_id=uuid.UUID(data["sub"]),
            reseller_id=uuid.UUID(data["rid"]),
            site_id=uuid.UUID(data["sid"]),
            expires_at=datetime.fromtimestamp(data["exp"], UTC),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidTokenError("token com dados inválidos") from exc


def derive_agent_key(secret: bytes) -> bytes:
    """K = SHA-256("dm-agent-auth\\n" + secret): the only derivative of the secret kept by the server."""
    return hashlib.sha256(b"dm-agent-auth\n" + secret).digest()


def agent_signature(key: bytes, agent_id: str, ts: int, nonce: str) -> str:
    return hmac.new(key, f"{agent_id}\n{ts}\n{nonce}".encode(), hashlib.sha256).hexdigest()


def set_server_signature(key: bytes, agent_id: str, server_url: str, ws_url: str, issued_at: int) -> str:
    """Assinatura do comando "Mudar endereço do servidor" com a chave do próprio coletor: ele confere antes
    de trocar (vale também em http:// na rede local, onde não há TLS)."""
    msg = f"dm-set-server\n{agent_id}\n{server_url}\n{ws_url}\n{issued_at}"
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def decode_access_token(token: str, *, secret: str) -> AccessClaims:
    try:
        data = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("token expirado") from exc
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("token inválido") from exc
    if data.get("typ") != "access":
        raise InvalidTokenError("tipo de token inválido")
    try:
        return AccessClaims(
            user_id=uuid.UUID(data["sub"]),
            reseller_id=uuid.UUID(data["rid"]),
            role=str(data["role"]),
            customer_id=uuid.UUID(data["cid"]) if data.get("cid") else None,
            limited=data.get("lim"),
            issued_at=datetime.fromtimestamp(data["iat"], UTC),
            expires_at=datetime.fromtimestamp(data["exp"], UTC),
            token_id=str(data.get("jti", "")),
            password_version=int(data["pwv"]) if data.get("pwv") is not None else None,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidTokenError("token com dados inválidos") from exc
