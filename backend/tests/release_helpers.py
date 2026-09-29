"""Test signing key for releases (the real private key never exists in the repository or the server)."""

import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.schemas.agent import release_message

TEST_RELEASE_KEY = Ed25519PrivateKey.generate()
TEST_RELEASE_PUBLIC_B64 = base64.b64encode(
    TEST_RELEASE_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
).decode()


def sign(
    component: str, version: str, os_: str, arch: str, data: bytes, key: Ed25519PrivateKey | None = None
) -> str:
    """What `dm-tool sign` produces for this binary."""
    digest = hashlib.sha256(data).hexdigest()
    sig = (key or TEST_RELEASE_KEY).sign(release_message(component, version, os_, arch, digest))
    return base64.b64encode(sig).decode()
