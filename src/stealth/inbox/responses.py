"""Answering a company that asked for ID or more details, so the person doesn't have to write it.

When a company asks for proof of identity, the drafted reply explains Article 12(6): they may ask
only for what's necessary, and a passport copy is rarely that for an erasure. When they ask for
more details, it gives the details from the profile, the same ones the first letter used. Nothing is
sent until the person has read the draft and pressed Send; sending restarts the company's clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from email.message import EmailMessage

from stealth.cases import deadlines
from stealth.cases.engine import (
    _account,
    _clock_restarted,
    _deliver,
    apply_outcome,
    jurisdiction_of,
)
from stealth.cases.state import TRANSITIONS, Case, Status
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.inbox import classifier
from stealth.mail.base import Transport, compose
from stealth.profiles.store import Profile
from stealth.templates.render import format_date, identity_context, render

ASKS = {classifier.ID_REQUESTED: "id", classifier.INFO_REQUESTED: "info"}


@dataclass(frozen=True)
class Reading:
    """Stealth's reading of a stored reply."""

    kind: str
    confident: bool
    quote: str
    urls: tuple[str, ...]
    message_id: int
    rfc_message_id: str | None

    @property
    def label(self) -> str:
        return classifier.LABELS.get(self.kind, classifier.LABELS[classifier.UNKNOWN])


def latest_reading(profile: Profile, case: Case) -> Reading | None:
    row = profile.conn.execute(
        "SELECT id, rfc_message_id, classification FROM message "
        "WHERE case_id = ? AND direction = 'in' AND kind = 'reply' "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (case.id,),
    ).fetchone()
    if row is None or not row["classification"]:
        return None
    data = json.loads(row["classification"])
    return Reading(
        kind=str(data.get("kind", classifier.UNKNOWN)),
        confident=bool(data.get("confident")),
        quote=str(data.get("quote", "")),
        urls=tuple(data.get("urls", ())),
        message_id=row["id"],
        rfc_message_id=row["rfc_message_id"],
    )


def what_they_asked(profile: Profile, case: Case) -> str | None:
    """ "id" or "info" when a drafted answer fits this case right now, otherwise None."""
    if not case.contact or not case.last_request_on:
        return None
    reading = latest_reading(profile, case)
    if reading and reading.kind in ASKS and case.status in (Status.REPLIED, Status.ID_REQUESTED):
        return ASKS[reading.kind]
    if case.status is Status.ID_REQUESTED:
        return "id"
    return None


def response_message(profile: Profile, case: Case, asked: str) -> EmailMessage:
    person, account, jurisdiction = profile.person(), _account(profile), jurisdiction_of(profile)
    assert case.contact and case.last_request_on
    today = clock.local_date(jurisdiction.tz)
    letter = render(
        jurisdiction,
        "response",
        ref=case.ref,
        asked=asked,
        request_date=format_date(case.last_request_on),
        deadline=format_date(deadlines.response_deadline(today, jurisdiction)),
        **identity_context(person, account, extended=case.tier == "extended"),
    )
    message = compose(letter, account.address, case.contact)
    reading = latest_reading(profile, case)
    if reading and reading.rfc_message_id and reading.rfc_message_id.startswith("<"):
        message["In-Reply-To"] = reading.rfc_message_id  # threads under their email
        message["References"] = reading.rfc_message_id
    return message


def send_response(profile: Profile, case: Case, transport: Transport) -> Case:
    asked = what_they_asked(profile, case)
    if asked is None:
        raise ProfileError(f"{case.broker_name} hasn't asked for anything Stealth can answer.")
    message = response_message(profile, case, asked)
    _deliver(profile, case, message, "response", transport, _account(profile))
    if case.status is not Status.ID_REQUESTED and Status.ID_REQUESTED in TRANSITIONS[case.status]:
        case = apply_outcome(
            profile, case, "id_requested", actor="you", reason="they asked for details"
        )
    what = "proof of identity" if asked == "id" else "more details"
    return _clock_restarted(
        profile, case, jurisdiction_of(profile), reason=f"you answered their request for {what}"
    )
