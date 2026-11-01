"""Database keys and recovery codes.

The database key lives in the OS keychain. If that is lost (reinstalled OS, new computer) the
evidence trail would go with it, so a copy is wrapped under a recovery code the person writes down.
The wrapped copy sits beside the database and is useless without the code.
"""

from __future__ import annotations

import base64
import json
import os
import secrets as pysecrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from stealth.core.errors import DatabaseKeyError, ProfileError
from stealth.core.secrets import SecretStore
from stealth.db.connection import KEY_BYTES

# Crockford-style alphabet: no I, L, O or U, so a code copied by hand can't be misread.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 25  # 125 bits
_SCRYPT = {"length": 32, "n": 2**15, "r": 8, "p": 1}


def key_name(profile_id: str) -> str:
    return f"db-key:{profile_id}"


def new_db_key() -> bytes:
    return os.urandom(KEY_BYTES)


def new_recovery_code() -> str:
    """A code like `7K2QF-...`, in five groups of five."""
    raw = "".join(pysecrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
    return "-".join(raw[i : i + 5] for i in range(0, _CODE_LENGTH, 5))


def _normalise(code: str) -> str:
    return code.replace("-", "").replace(" ", "").upper()


def _derive(code: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, **_SCRYPT).derive(_normalise(code).encode())


def wrap_key(db_key: bytes, code: str, path: Path) -> None:
    """Save `db_key`, encrypted under the recovery code, to `path`."""
    salt, nonce = os.urandom(16), os.urandom(12)
    sealed = AESGCM(_derive(code, salt)).encrypt(nonce, db_key, None)
    body = {k: base64.b64encode(v).decode() for k, v in (("s", salt), ("n", nonce), ("c", sealed))}
    path.write_text(json.dumps(body), encoding="utf-8")


def unwrap_key(code: str, path: Path) -> bytes:
    """Recover the database key from `path` using the recovery code."""
    if not path.exists():
        raise ProfileError("This profile has no recovery file.")
    body = {k: base64.b64decode(v) for k, v in json.loads(path.read_text("utf-8")).items()}
    try:
        return AESGCM(_derive(code, body["s"])).decrypt(body["n"], body["c"], None)
    except InvalidTag as exc:
        raise DatabaseKeyError(
            "That recovery code doesn't match this profile.",
            hint="Check each character. Letters I, L, O and U are never used.",
        ) from exc


def load_db_key(store: SecretStore, profile_id: str) -> bytes:
    """Fetch a profile's database key from the keychain."""
    value = store.get(key_name(profile_id))
    if value is None:
        raise DatabaseKeyError(
            "This profile's key isn't in your computer's password store.",
            hint="Restore it with the recovery code you saved during setup.",
        )
    return bytes.fromhex(value)


def save_db_key(store: SecretStore, profile_id: str, key: bytes) -> None:
    store.set(key_name(profile_id), key.hex())
