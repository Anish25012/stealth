"""The append-only audit log.

Each event's hash covers the previous event's hash, so an evidence pack can show that nothing was
altered or removed after the fact. The triggers in the schema stop accidents; the chain makes
deliberate edits detectable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from stealth.core import clock
from stealth.db.connection import Connection

GENESIS = "0" * 64


def _digest(
    prev_hash: str, case_id: int | None, type_: str, at: str, actor: str, payload: str
) -> str:
    material = json.dumps([prev_hash, case_id, type_, at, actor, payload], separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


def append(
    conn: Connection,
    type_: str,
    *,
    case_id: int | None = None,
    actor: str = "stealth",
    **payload: Any,
) -> None:
    """Record something that happened."""
    row = conn.execute("SELECT hash FROM event ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = row["hash"] if row else GENESIS
    at = clock.now().isoformat()
    body = json.dumps(payload, sort_keys=True, default=str)
    conn.execute(
        "INSERT INTO event (case_id, type, at, actor, payload, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            case_id,
            type_,
            at,
            actor,
            body,
            prev_hash,
            _digest(prev_hash, case_id, type_, at, actor, body),
        ),
    )


def verify_chain(conn: Connection) -> bool:
    """True if every event still links to the one before it and matches its own hash."""
    prev_hash = GENESIS
    for row in conn.execute("SELECT * FROM event ORDER BY id"):
        expected = _digest(
            prev_hash, row["case_id"], row["type"], row["at"], row["actor"], row["payload"]
        )
        if row["prev_hash"] != prev_hash or row["hash"] != expected:
            return False
        prev_hash = row["hash"]
    return True


def for_case(conn: Connection, case_id: int) -> list[dict[str, Any]]:
    """A case's events, oldest first, with payloads decoded."""
    rows = conn.execute("SELECT * FROM event WHERE case_id = ? ORDER BY id", (case_id,))
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
