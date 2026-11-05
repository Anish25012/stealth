"""The case state machine. Every status change in the app goes through `transition`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from stealth.core import clock
from stealth.core.errors import InvalidTransitionError, ProfileError
from stealth.db import events
from stealth.db.connection import Connection


class Status(StrEnum):
    DRAFT = "draft"  # a letter is ready and waits for the person's OK before it can be sent
    QUEUED = "queued"
    AWAITING_FORM = "awaiting_form"  # the person must fill in a web form
    SENT = "sent"
    REPLIED = "replied"  # a reply arrived and the person hasn't said what it means yet
    ID_REQUESTED = "id_requested"  # the clock is stopped until ID is supplied
    EXTENDED = "extended"  # the company took its two-month extension
    REMOVED = "removed"
    NO_DATA = "no_data"
    REFUSED = "refused"
    BOUNCED = "bounced"
    COMPLAINT_SENT = "complaint_sent"
    ICO_READY = "ico_ready"
    ESCALATED = "escalated"  # the person has complained to the ICO
    NEEDS_YOU = "needs_you"
    SKIPPED = "skipped"


S = Status
# Anything a person can decide from a reply, reachable from every open state.
_OUTCOMES = {S.ID_REQUESTED, S.EXTENDED, S.REMOVED, S.NO_DATA, S.REFUSED, S.NEEDS_YOU}

TRANSITIONS: dict[Status, set[Status]] = {
    S.DRAFT: {S.QUEUED, S.AWAITING_FORM, S.SKIPPED},
    S.QUEUED: {S.SENT, S.AWAITING_FORM, S.SKIPPED},
    S.AWAITING_FORM: {S.SENT, S.SKIPPED},
    S.SENT: {S.REPLIED, S.BOUNCED, S.COMPLAINT_SENT} | _OUTCOMES,
    S.REPLIED: {S.COMPLAINT_SENT, S.ICO_READY} | _OUTCOMES,
    S.ID_REQUESTED: {S.SENT, S.REPLIED, S.COMPLAINT_SENT} | _OUTCOMES,
    S.EXTENDED: {S.REPLIED, S.COMPLAINT_SENT} | _OUTCOMES,
    S.REFUSED: {S.COMPLAINT_SENT, S.ICO_READY, S.REMOVED, S.NO_DATA},
    S.COMPLAINT_SENT: {S.REPLIED, S.BOUNCED, S.ICO_READY, S.REMOVED, S.NO_DATA, S.NEEDS_YOU},
    S.ICO_READY: {S.ESCALATED, S.REMOVED, S.NO_DATA},
    S.ESCALATED: {S.REMOVED, S.NO_DATA},
    # A repeat request or a retry goes back to DRAFT when the person approves each send.
    S.REMOVED: {S.DRAFT, S.QUEUED, S.AWAITING_FORM},
    S.NO_DATA: {S.DRAFT, S.QUEUED, S.AWAITING_FORM},
    S.BOUNCED: {S.DRAFT, S.QUEUED, S.AWAITING_FORM, S.SKIPPED},
    S.NEEDS_YOU: {S.SENT, S.COMPLAINT_SENT, S.ICO_READY} | (_OUTCOMES - {S.NEEDS_YOU}),
    S.SKIPPED: {S.DRAFT, S.QUEUED, S.AWAITING_FORM},
}

_DATE_COLUMNS = (
    "relevant_time",
    "deadline_on",
    "complaint_on",
    "last_request_on",
    "next_action_on",
)
_ALLOWED_UPDATES = {*_DATE_COLUMNS, "round", "contact"}


@dataclass
class Case:
    id: int
    broker_id: str
    broker_name: str
    broker_domain: str
    category: str
    method: str
    contact: str | None
    tier: str
    template: str
    ref: str
    status: Status
    round: int
    relevant_time: date | None
    deadline_on: date | None
    complaint_on: date | None
    last_request_on: date | None
    next_action_on: date | None
    custom_url: str | None


def _to_case(row: Any) -> Case:
    values = {k: row[k] for k in row.keys() if k != "created_at"}  # noqa: SIM118
    values["status"] = Status(values["status"])
    for column in _DATE_COLUMNS:
        values[column] = date.fromisoformat(values[column]) if values[column] else None
    return Case(**values)


def all_cases(conn: Connection) -> list[Case]:
    return [_to_case(r) for r in conn.execute("SELECT * FROM cases ORDER BY broker_name")]


def get_case(conn: Connection, case_id: int) -> Case:
    return _to_case(conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone())


def find_case(conn: Connection, broker: str) -> Case:
    """Look a case up by broker id, or by a unique part of the broker's name."""
    row = conn.execute("SELECT * FROM cases WHERE broker_id = ?", (broker,)).fetchone()
    if row:
        return _to_case(row)
    rows = conn.execute(
        "SELECT * FROM cases WHERE lower(broker_name) LIKE ?", (f"%{broker.lower()}%",)
    ).fetchall()
    if len(rows) == 1:
        return _to_case(rows[0])
    if rows:
        names = ", ".join(r["broker_id"] for r in rows[:5])
        raise ProfileError(f"'{broker}' matches several companies: {names}.")
    raise ProfileError(f"No case found for '{broker}'.", hint="`stealth status` lists every case.")


def create_case(
    conn: Connection,
    *,
    broker_id: str,
    broker_name: str,
    broker_domain: str,
    category: str,
    method: str,
    contact: str | None,
    tier: str,
    template: str,
    ref: str,
    custom_url: str | None = None,
) -> Case:
    cursor = conn.execute(
        "INSERT INTO cases (broker_id, broker_name, broker_domain, category, method, contact, "
        "tier, template, ref, status, custom_url, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            broker_id,
            broker_name,
            broker_domain,
            category,
            method,
            contact,
            tier,
            template,
            ref,
            Status.DRAFT.value,
            custom_url,
            clock.now().isoformat(),
        ),
    )
    case_id: int = cursor.lastrowid
    events.append(conn, "case_created", case_id=case_id, broker=broker_id, tier=tier)
    return get_case(conn, case_id)


def transition(
    conn: Connection,
    case: Case,
    to: Status,
    *,
    actor: str = "stealth",
    reason: str = "",
    **updates: Any,
) -> Case:
    """Move a case, recording why, and optionally update its dates in the same step."""
    if to not in TRANSITIONS[case.status]:
        raise InvalidTransitionError(
            f"{case.broker_name} is '{case.status.value}' and can't become '{to.value}'."
        )
    unknown = set(updates) - _ALLOWED_UPDATES
    if unknown:
        raise ValueError(f"not case columns: {sorted(unknown)}")
    columns = {"status": to.value, **{k: _stored(v) for k, v in updates.items()}}
    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn.execute("BEGIN")
    try:
        conn.execute(
            f"UPDATE cases SET {assignments} WHERE id = ?",  # noqa: S608 - fixed column names
            (*columns.values(), case.id),
        )
        events.append(
            conn,
            "status_changed",
            case_id=case.id,
            actor=actor,
            before=case.status.value,
            after=to.value,
            reason=reason,
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get_case(conn, case.id)


def _stored(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value
