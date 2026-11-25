"""Autopilot's follow-up letters: answering what a company asked, or chasing a vague answer.

Only on Autopilot, only for a reply that provably answers our letter (threaded, or quoting the
case reference), and at most once per request round: if the company comes back again, the person
decides. Three kinds:

- **ID pushback** (`id`): they asked you to prove who you are, without naming documents. The reply
  is the Article 12(6) letter: an erasure from your own address rarely needs ID, and you don't
  consent to sending documents. If they name documents (passport, driving licence, a bill, a
  photo of ID), it goes to the person instead: only they decide whether to send any.
- **More details** (`info`): they asked which details to search for. The reply gives the same
  minimum details the first letter used for that kind of company, and nothing more.
- **Chase** (`chase`): they only confirmed an opt-out or unsubscribe, not an erasure. The reply
  asks them to confirm the full erasure. Their original deadline doesn't move.

Nothing here ever sends an ID document, or any detail the person hasn't given Stealth.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from stealth import autopilot
from stealth.cases.engine import jurisdiction_of
from stealth.cases.state import Case, Status, all_cases
from stealth.core import clock
from stealth.core.errors import StealthError
from stealth.db import events
from stealth.inbox import classifier, responses
from stealth.inbox.responses import Reading
from stealth.mail.base import Transport
from stealth.profiles.store import Profile

REASONS = {
    "id": "they asked you to prove who you are; Stealth explained why that isn't needed",
    "info": "they asked for more details; Stealth sent the ones your first letter used",
    "chase": "they only confirmed an opt-out; Stealth asked them to confirm the erasure",
}
VAGUE_ONLY = {classifier.REMOVED, classifier.ACKNOWLEDGED, classifier.AUTO_REPLY}


@dataclass(frozen=True)
class FollowUp:
    case: Case
    kind: str  # id, info or chase
    reading: Reading


def vague(reading: Reading) -> bool:
    """An answer that only stops marketing: "removed", but weakly, and nothing else in it."""
    return (
        reading.kind == classifier.REMOVED
        and not reading.confident
        and bool(reading.scores)
        and set(reading.scores) <= VAGUE_ONLY
    )


def _already(profile: Profile, case: Case) -> bool:
    """Autopilot has followed up once this round: a second time is the person's call."""
    for event in events.for_case(profile.conn, case.id):
        payload = event["payload"]
        if (
            event["type"] == autopilot.EVENT
            and payload.get("what") in ("response", "chase")
            and payload.get("round") == case.round
        ):
            return True
    return False


def _kind(case: Case, reading: Reading, today_overdue: bool) -> str | None:
    if case.status is Status.ID_REQUESTED and reading.kind == classifier.ID_REQUESTED:
        return None if reading.documents else "id"
    if case.status is not Status.REPLIED:
        return None
    if reading.kind == classifier.INFO_REQUESTED and reading.confident:
        return "info"
    if vague(reading) and not today_overdue:
        return "chase"  # once the deadline has passed, a person reads it before any complaint
    return None


def due(profile: Profile) -> list[FollowUp]:
    """The follow-ups Autopilot would send now. Empty when Autopilot is off."""
    if not autopilot.is_on(profile.settings()):
        return []
    today = clock.local_date(jurisdiction_of(profile).tz)
    found = []
    for case in all_cases(profile.conn):
        if not case.contact or not case.last_request_on:
            continue
        reading = responses.latest_reading(profile, case)
        if reading is None or not reading.proven:
            continue
        overdue = case.deadline_on is not None and today > case.deadline_on
        kind = _kind(case, reading, overdue)
        if kind and not _already(profile, case):
            found.append(FollowUp(case, kind, reading))
    return found


def send_due(
    profile: Profile, transport: Transport, sleep: Callable[[float], None]
) -> tuple[list[str], list[str]]:
    """Send what's due. Returns (companies followed up, companies where sending failed)."""
    sent, failed = [], []
    delay = profile.settings().send_delay_seconds
    for number, item in enumerate(due(profile)):
        if number:
            sleep(delay)
        round_ = item.case.round
        try:
            if item.kind == "chase":
                case = responses.send_chase(profile, item.case, transport, actor=autopilot.ACTOR)
            else:
                case = responses.send_response(profile, item.case, transport, actor=autopilot.ACTOR)
        except StealthError:
            failed.append(item.case.broker_name)
            continue
        what = "chase" if item.kind == "chase" else "response"
        autopilot.record(profile, case, what, REASONS[item.kind], round=round_, asked=item.kind)
        sent.append(case.broker_name)
    return sent, failed
