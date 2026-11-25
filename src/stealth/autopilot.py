"""Autopilot: Stealth runs itself, and leaves the person only what a person must do.

With Autopilot on, the daily check (in the desktop app) does without asking:

- first letters to core companies, in daily batches within the daily send limit;
- repeat requests when they fall due;
- formal complaints after a missed deadline or a refusal (the inbox, spam included, is read
  first, so a reply that arrived late still counts).

Still the person's, even on Autopilot: anything involving ID documents, web forms, deleting old
accounts they haven't marked Delete, replies Stealth can't read with confidence, ICO complaints
(the ICO only takes them through its own website), and letters to optional (extended) companies.

It answers or chases a company once per request (see `followups`): polite pushback on an ID
request that names no documents, the first letter's details when asked for more, and a chase when
a reply only confirms an opt-out. A start date (`autopilot_from`) holds first letters and repeats
until that day; everything else carries on.

It also opens a company's "click to confirm your request" link, under the strict rules in
`inbox.verify`.

Every letter Autopilot sends is in the audit trail as an `autopilot` event by `ACTOR`, which is
what the "Sent automatically" list reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from stealth.cases.state import Case, Status, find_case
from stealth.db import events
from stealth.profiles.models import Settings
from stealth.profiles.store import Profile

ACTOR = "stealth (autopilot)"
EVENT = "autopilot"

WHAT = {
    "request": "First letter",
    "repeat": "Repeat request",
    "complaint": "Formal complaint",
    "response": "Reply to their request",
    "chase": "Chased a vague answer",
    "verify": "Confirmation link opened",
    "custom": "Letter to a company you added",
}


def is_on(settings: Settings) -> bool:
    return settings.autopilot


def starts_on(settings: Settings) -> date | None:
    """The day Autopilot may start sending first letters and repeats, if the person chose one."""
    if not settings.autopilot_from:
        return None
    try:
        return date.fromisoformat(settings.autopilot_from)
    except ValueError:
        return None


def sends_letters(settings: Settings, today: date) -> bool:
    """Autopilot is on, and its start date (if any) has come."""
    start = starts_on(settings)
    return settings.autopilot and (start is None or today >= start)


def complaints_automatic(settings: Settings) -> bool:
    """Complaints go without asking on Autopilot, or when the person chose just that."""
    return settings.autopilot or settings.auto_complaints


def handles_draft(settings: Settings, case: Case) -> bool:
    """A draft Autopilot will send by itself: core companies only. Optional ones still ask."""
    return settings.autopilot and case.status is Status.DRAFT and case.tier == "core"


def record(profile: Profile, case: Case, what: str, detail: str = "", **extra: Any) -> None:
    """Note in the audit trail that Autopilot did this, for the "Sent automatically" list."""
    assert what in WHAT, what
    events.append(
        profile.conn, EVENT, case_id=case.id, actor=ACTOR, what=what, reason=detail, **extra
    )


@dataclass(frozen=True)
class Done:
    case: Case
    what: str  # a key of WHAT
    label: str
    detail: str
    at: datetime


def sent_automatically(profile: Profile, limit: int = 500) -> list[Done]:
    """Everything Autopilot sent or did, newest first."""
    rows = profile.conn.execute(
        "SELECT e.at, e.payload, c.broker_id FROM event e JOIN cases c ON c.id = e.case_id "
        "WHERE e.type = ? ORDER BY e.id DESC LIMIT ?",
        (EVENT, limit),
    ).fetchall()
    done = []
    for row in rows:
        payload: dict[str, Any] = json.loads(row["payload"])
        what = str(payload.get("what", ""))
        done.append(
            Done(
                case=find_case(profile.conn, row["broker_id"]),
                what=what,
                label=WHAT.get(what, what),
                detail=str(payload.get("reason", "")),
                at=datetime.fromisoformat(row["at"]),
            )
        )
    return done
