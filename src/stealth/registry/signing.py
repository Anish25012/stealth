"""The broker list's signature.

A checksum published next to the list proves nothing: anyone who can change one can change the
other. So the list is signed with ed25519. The public key below ships inside the app; the private
key lives only in the project's GitHub Actions secrets (and its maintainer's offline backup).
A downloaded list is used only if its signature checks out against this key.
"""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PUBLIC_KEY = "G3TN2ObQ7+zhvp/0KBQls5yv3qWhUd8HAM2pqrmrCuI="


def verify(data: bytes, signature: str | bytes, public_key: str | None = None) -> bool:
    """Whether `signature` (base64) is a valid signature of exactly these bytes."""
    try:
        raw = base64.b64decode(signature.strip(), validate=True)
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key or PUBLIC_KEY))
        key.verify(raw, data)
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True


def sign(data: bytes, private_key: str) -> str:
    """A base64 signature of these bytes. Used by `tools/sign_broker_list.py` in CI."""
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key.strip()))
    return base64.b64encode(key.sign(data)).decode("ascii")
