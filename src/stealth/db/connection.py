"""Opening encrypted databases and applying schema migrations."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import sqlcipher3

from stealth.core.errors import DatabaseKeyError

Connection = Any  # sqlcipher3 ships no type stubs; its API mirrors sqlite3.Connection
KEY_BYTES = 32


def open_db(path: Path, key: bytes) -> Connection:
    """Open (or create) the database at `path`, unlocked with a raw 32-byte key."""
    if len(key) != KEY_BYTES:
        raise ValueError("database keys must be 32 bytes")
    conn = sqlcipher3.connect(str(path), isolation_level=None)
    # Raw-key form skips the slow passphrase KDF: the key is already 256 bits of randomness.
    # It is hex from os.urandom, never user input, so interpolating it is safe.
    conn.execute("PRAGMA cipher_log_level = NONE")
    conn.execute(f"""PRAGMA key = "x'{key.hex()}'" """)
    conn.row_factory = sqlcipher3.Row
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher3.DatabaseError as exc:
        conn.close()
        raise DatabaseKeyError(
            "Stealth couldn't unlock this profile's data.",
            hint="If you moved to a new computer, restore it with your recovery code.",
        ) from exc
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def available_migrations() -> list[tuple[int, str, str]]:
    """Packaged migrations as (version, filename, sql), oldest first."""
    folder = resources.files("stealth.db") / "migrations"
    found = []
    for entry in folder.iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.name, entry.read_text("utf-8")))
    return sorted(found)


def migrate(conn: Connection) -> int:
    """Bring the database up to the newest schema. Returns the resulting version."""
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, _name, sql in available_migrations():
        if version <= current:
            continue
        # executescript commits first, so BEGIN lives in the script to make each step atomic.
        script = f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;"
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        current = version
    return current
