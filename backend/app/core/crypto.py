"""AES-256-GCM encryption for secrets stored in the database (SNMP communities, TOTP, WhatsApp)."""

import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"
_NONCE_LEN = 12


class DecryptionError(Exception):
    """Ciphertext is corrupt or was produced with a different master key."""


def encrypt(key: bytes, plaintext: str, *, associated_data: bytes = b"") -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    return _VERSION + nonce + AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), associated_data)


def decrypt(key: bytes, blob: bytes, *, associated_data: bytes = b"") -> str:
    if len(blob) < 1 + _NONCE_LEN + 16 or blob[:1] != _VERSION:
        raise DecryptionError("formato de segredo cifrado desconhecido")
    nonce = blob[1 : 1 + _NONCE_LEN]
    try:
        return AESGCM(key).decrypt(nonce, blob[1 + _NONCE_LEN :], associated_data).decode("utf-8")
    except InvalidTag as exc:
        raise DecryptionError("segredo não pôde ser decifrado (chave mestre diferente?)") from exc


def encrypt_json(key: bytes, value: dict[str, Any], *, associated_data: bytes = b"") -> bytes:
    return encrypt(key, json.dumps(value, separators=(",", ":")), associated_data=associated_data)


def decrypt_json(key: bytes, blob: bytes, *, associated_data: bytes = b"") -> dict[str, Any]:
    data = json.loads(decrypt(key, blob, associated_data=associated_data))
    if not isinstance(data, dict):
        raise DecryptionError("segredo cifrado não contém um objeto JSON")
    return data
