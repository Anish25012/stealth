"""Sending letters and acting on what comes back.

Dry runs write `.eml` files and change nothing else. Only a live send creates or moves cases, so a
person can preview as often as they like without affecting a deadline.
"""

from __future__ import annotations

import hashlib
import logging
import secrets as pysecrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from stealth.cases import deadlines
from stealth.cases.state import (
    Case,
    Status,
    all_cases,
    create_case,
    find_case,
    transition,
)
from stealth.core import clock
from stealth.core.errors import MailSendError, ProfileError
from stealth.core.jurisdictions import Jurisdiction, for_country
from stealth.core.paths import ensure_private_dir
from stealth.db import events
from stealth.mail.base import Transport, compose
from stealth.profiles.models import MailAccount, Person
from stealth.profiles.store import Profile
from stealth.registry.loader import Broker, Selection, select_brokers
from stealth.templates.render import format_date, identity_context, render

log = logging.getLogger(__name__)

Sleep = Callable[[float], None]


@dataclass
class SendReport:
    sent: list[str] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    held_back: int = 0  # due, but over today's sending limit


def new_ref() -> str:
    return f"STL-{pysecrets.token_hex(4).upper()}"


def jurisdiction_of(profile: Profile) -> Jurisdiction:
    return for_country(profile.person().country)


def _account(profile: Profile) -> MailAccount:
    account = profile.mail_account()
    if account is None:
        raise ProfileError("No email account connected yet.", hint="Run `stealth init`.")
    return account


# Letters ------------------------------------------------------------------------------------


def request_message(
    person: Person,
    account: MailAccount,
    jurisdiction: Jurisdiction,
    *,
    contact: str,
    tier: str,
    ref: str,
    today: date,
) -> EmailMessage:
    deadline = deadlines.response_deadline(today, jurisdiction)
    letter = render(
        jurisdiction,
        "erasure",
        ref=ref,
        deadline=format_date(deadline),
        **identity_context(person, account, extended=tier == "extended"),
    )
    return compose(letter, account.address, contact)


def complaint_message(
    person: Person,
    account: MailAccount,
    jurisdiction: Jurisdiction,
    case: Case,
    *,
    grievance: str,
) -> EmailMessage:
    assert case.contact and case.last_request_on
    context = identity_context(person, account, extended=False)
    letter = render(
        jurisdiction,
        "complaint",
        ref=case.ref,
        request_date=format_date(case.last_request_on),
        grievance=grievance,
        **context,
    )
    return compose(letter, account.address, case.contact)


# Evidence -----------------------------------------------------------------------------------


def _evidence_file(profile: Profile, broker_id: str, label: str) -> Path:
    folder = ensure_private_dir(profile.evidence / broker_id)
    stamp = clock.now().strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"{stamp}-{label}.eml"
    counter = 1
    while path.exists():
        path = folder / f"{stamp}-{label}-{counter}.eml"
        counter += 1
    return path


def save_message(
    profile: Profile,
    case: Case,
    *,
    direction: str,
    kind: str,
    raw: bytes,
    rfc_message_id: str | None,
    sender: str | None,
    subject: str | None,
) -> Path:
    path = _evidence_file(profile, case.broker_id, kind)
    path.write_bytes(raw)
    profile.conn.execute(
        "INSERT INTO message (case_id, direction, kind, rfc_message_id, sender, subject, "
        "occurred_at, path, sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            case.id,
            direction,
            kind,
            rfc_message_id,
            sender,
            subject,
            clock.now().isoformat(),
            str(path.relative_to(profile.folder)),
            hashlib.sha256(raw).hexdigest(),
        ),
    )
    return path


def sent_today(profile: Profile, jurisdiction: Jurisdiction) -> int:
    today = clock.local_date(jurisdiction.tz)
    rows = profile.conn.execute("SELECT occurred_at FROM message WHERE direction = 'out'")
    return sum(
        clock.to_local_date(datetime.fromisoformat(r["occurred_at"]), jurisdiction.tz) == today
        for r in rows
    )


def _deliver(
    profile: Profile,
    case: Case,
    message: EmailMessage,
    kind: str,
    transport: Transport,
    account: MailAccount,
) -> None:
    transport.send(message, bcc=account.address if transport.wants_bcc else None)
    save_message(
        profile,
        case,
        direction="out",
        kind=kind,
        raw=message.as_bytes(),
        rfc_message_id=str(message["Message-ID"]),
        sender=account.address,
        subject=str(message["Subject"]),
    )


# Requests -----------------------------------------------------------------------------------


def write_dry_run(profile: Profile, broker_id: str, message: EmailMessage, label: str) -> Path:
    path = profile.outbox / f"{broker_id}-{label}.eml"
    path.write_bytes(message.as_bytes())
    return path


def due_selections(profile: Profile, selections: list[Selection]) -> list[Selection]:
    """Brokers with no case yet: the people Stealth hasn't written to."""
    existing = {c.broker_id for c in all_cases(profile.conn)}
    return [s for s in selections if s.broker.id not in existing]


def preview_requests(profile: Profile, brokers: dict[str, Broker]) -> SendReport:
    """Dry run: one `.eml` per email broker due a letter, with no change to any case."""
    person, account, jurisdiction = profile.person(), _account(profile), jurisdiction_of(profile)
    selections = select_brokers(brokers, jurisdiction, profile.settings())
    today = clock.local_date(jurisdiction.tz)
    report = SendReport()
    for selection in due_selections(profile, selections):
        broker = selection.broker
        if broker.method == "form" or not broker.email:
            report.forms.append(broker.name)
            continue
        message = request_message(
            person,
            account,
            jurisdiction,
            contact=broker.email,
            tier=selection.tier,
            ref=new_ref(),
            today=today,
        )
        report.written.append(write_dry_run(profile, broker.id, message, "request"))
    return report


def _create_for(profile: Profile, selection: Selection) -> Case:
    broker = selection.broker
    case = create_case(
        profile.conn,
        broker_id=broker.id,
        broker_name=broker.name,
        broker_domain=broker.domain,
        category=broker.category,
        method=broker.method,
        contact=broker.email,
        tier=selection.tier,
        template="erasure",
        ref=new_ref(),
    )
    if broker.method == "form" or not broker.email:
        return transition(profile.conn, case, Status.AWAITING_FORM, reason="web form only")
    return transition(profile.conn, case, Status.QUEUED, reason="new broker")


def mark_sent(profile: Profile, case: Case, jurisdiction: Jurisdiction, *, actor: str) -> Case:
    """Start the clock on a request that has just gone out."""
    today = clock.local_date(jurisdiction.tz)
    deadline = deadlines.response_deadline(today, jurisdiction)
    return transition(
        profile.conn,
        case,
        Status.SENT,
        actor=actor,
        reason=f"request sent, reply due {deadline.isoformat()}",
        round=case.round + 1,
        relevant_time=today,
        deadline_on=deadline,
        complaint_on=None,
        last_request_on=today,
        next_action_on=deadline,
    )


def send_requests(
    profile: Profile,
    brokers: dict[str, Broker],
    transport: Transport,
    *,
    sleep: Sleep,
    only: str | None = None,
) -> SendReport:
    """Live send to new brokers and anything queued, within today's limit.

    With `only`, the rest of the queue is left alone, which is what makes a test send to
    your own inbox safe.
    """
    person, account, jurisdiction = profile.person(), _account(profile), jurisdiction_of(profile)
    settings = profile.settings()
    report = SendReport()

    for selection in due_selections(profile, select_brokers(brokers, jurisdiction, settings)):
        case = _create_for(profile, selection)
        if case.status is Status.AWAITING_FORM:
            report.forms.append(case.broker_name)

    queue = [
        c
        for c in all_cases(profile.conn)
        if c.status is Status.QUEUED and only in (None, c.broker_id)
    ]
    remaining = settings.daily_send_limit - sent_today(profile, jurisdiction)
    report.held_back = max(0, len(queue) - max(0, remaining))
    today = clock.local_date(jurisdiction.tz)

    for index, case in enumerate(queue[: max(0, remaining)]):
        if index:
            sleep(settings.send_delay_seconds)
        assert case.contact
        message = request_message(
            person,
            account,
            jurisdiction,
            contact=case.contact,
            tier=case.tier,
            ref=case.ref,
            today=today,
        )
        try:
            _deliver(profile, case, message, "request", transport, account)
        except MailSendError as exc:
            # One bad address must not stop the rest. Sign-in or network failures still propagate,
            # because they would fail every message after this one too.
            events.append(profile.conn, "send_failed", case_id=case.id, reason=exc.message)
            report.failed.append(case.broker_name)
            continue
        mark_sent(profile, case, jurisdiction, actor="stealth")
        report.sent.append(case.broker_name)
    return report


# Decisions the person makes -----------------------------------------------------------------


OUTCOMES = {
    "removed": Status.REMOVED,
    "no_data": Status.NO_DATA,
    "refused": Status.REFUSED,
    "id_requested": Status.ID_REQUESTED,
    "extended": Status.EXTENDED,
    "escalated": Status.ESCALATED,
}


def confirm(profile: Profile, broker: str, outcome: str) -> Case:
    """Record what a reply actually meant. Only a person decides a case is finished."""
    case = find_case(profile.conn, broker)
    jurisdiction = jurisdiction_of(profile)
    settings = profile.settings()
    conn = profile.conn

    if outcome == "info_supplied":
        return _clock_restarted(profile, case, jurisdiction)
    if outcome not in OUTCOMES:
        choices = ", ".join([*OUTCOMES, "info_supplied"])
        raise ProfileError(f"Unknown outcome '{outcome}'.", hint=f"Choose from: {choices}.")

    target = OUTCOMES[outcome]
    if target is Status.REFUSED and case.complaint_on:
        # They have refused after a formal complaint, so there's nothing left to wait for.
        return transition(
            conn, case, Status.ICO_READY, actor="you", reason="refused after complaint"
        )
    if target in (Status.REMOVED, Status.NO_DATA):
        assert case.last_request_on
        return transition(
            conn,
            case,
            target,
            actor="you",
            reason="confirmed",
            next_action_on=deadlines.resend_on(case.last_request_on, case.category, settings),
        )
    if target is Status.ID_REQUESTED:
        # Art 12A: the period runs from when the company gets what it reasonably asked for.
        return transition(
            conn,
            case,
            target,
            actor="you",
            reason="clock stopped until ID is supplied",
            deadline_on=None,
            next_action_on=None,
        )
    if target is Status.EXTENDED:
        assert case.relevant_time
        deadline = deadlines.extended_deadline(case.relevant_time, jurisdiction)
        return transition(
            conn,
            case,
            target,
            actor="you",
            reason="company took its extension",
            deadline_on=deadline,
            next_action_on=deadline,
        )
    return transition(conn, case, target, actor="you", reason="confirmed", next_action_on=None)


def _clock_restarted(profile: Profile, case: Case, jurisdiction: Jurisdiction) -> Case:
    today = clock.local_date(jurisdiction.tz)
    deadline = deadlines.response_deadline(today, jurisdiction)
    return transition(
        profile.conn,
        case,
        Status.SENT,
        actor="you",
        reason=f"ID supplied, clock restarted, reply due {deadline.isoformat()}",
        relevant_time=today,
        deadline_on=deadline,
        next_action_on=deadline,
    )


def form_done(profile: Profile, broker: str) -> Case:
    """The person submitted a broker's web form, so its legal clock starts now."""
    case = find_case(profile.conn, broker)
    if case.status is not Status.AWAITING_FORM:
        raise ProfileError(f"{case.broker_name} isn't waiting for a web form.")
    return mark_sent(profile, case, jurisdiction_of(profile), actor="you")


def skip(profile: Profile, broker: str) -> Case:
    case = find_case(profile.conn, broker)
    return transition(profile.conn, case, Status.SKIPPED, actor="you", reason="skipped by you")


# Complaints ---------------------------------------------------------------------------------


NO_REPLY = "The time limit has passed and I have not received a substantive response."
REFUSED = "You have refused my request, and I do not accept your reasons for doing so."


def send_complaint(
    profile: Profile, case: Case, transport: Transport, *, grievance: str = NO_REPLY
) -> Case:
    person, account, jurisdiction = profile.person(), _account(profile), jurisdiction_of(profile)
    if not case.contact:
        raise ProfileError(
            f"Stealth has no email address for {case.broker_name}.",
            hint="Use the contact details on the company's own privacy page.",
        )
    message = complaint_message(person, account, jurisdiction, case, grievance=grievance)
    _deliver(profile, case, message, "complaint", transport, account)
    today = clock.local_date(jurisdiction.tz)
    return transition(
        profile.conn,
        case,
        Status.COMPLAINT_SENT,
        reason="formal complaint sent",
        complaint_on=today,
        next_action_on=deadlines.ico_ready_on(today, profile.settings()),
    )


def draft_ico_complaint(profile: Profile, broker: str) -> Path:
    """Write a complaint for the person to submit to the ICO themselves."""
    case = find_case(profile.conn, broker)
    if case.status not in (Status.ICO_READY, Status.ESCALATED):
        wait = ""
        if case.status is Status.COMPLAINT_SENT and case.next_action_on:
            wait = f" It will be ready on {format_date(case.next_action_on)}."
        raise ProfileError(
            f"{case.broker_name} isn't ready for an ICO complaint yet.{wait}",
            hint="The ICO expects a company to have had the chance to answer a complaint first.",
        )
    assert case.last_request_on and case.deadline_on
    person, account, jurisdiction = profile.person(), _account(profile), jurisdiction_of(profile)
    request_date = format_date(case.last_request_on)
    complaint_date = format_date(case.complaint_on) if case.complaint_on else "(not sent)"
    letter = render(
        jurisdiction,
        "ico",
        person_name=person.display_name,
        contact_email=account.address,
        broker_name=case.broker_name,
        broker_domain=case.broker_domain,
        ref=case.ref,
        request_date=request_date,
        deadline=format_date(case.deadline_on),
        complaint_date=complaint_date,
        outcome=(
            "The organisation refused my request."
            if _was_refused(profile, case)
            else "I did not receive a substantive response by that date."
        ),
        complaint_outcome=(
            "The organisation refused to put this right."
            if _was_refused(profile, case)
            else "I have not received a satisfactory response to my complaint."
        ),
        timeline=_timeline(profile, case, jurisdiction),
        sign_off=person.display_name,
    )
    path = ensure_private_dir(profile.evidence / case.broker_id) / "ico-complaint-draft.txt"
    path.write_text(f"{letter.subject}\n\n{letter.body}", encoding="utf-8")
    events.append(profile.conn, "ico_complaint_drafted", case_id=case.id)
    return path


def _was_refused(profile: Profile, case: Case) -> bool:
    return any(
        e["payload"].get("reason") == "refused after complaint"
        or e["payload"].get("after") == Status.REFUSED.value
        for e in events.for_case(profile.conn, case.id)
        if e["type"] == "status_changed"
    )


def _timeline(profile: Profile, case: Case, jurisdiction: Jurisdiction) -> list[str]:
    rows = profile.conn.execute(
        "SELECT direction, kind, occurred_at, subject FROM message WHERE case_id = ? ORDER BY id",
        (case.id,),
    )
    labels = {
        ("out", "request"): "Erasure request sent",
        ("out", "complaint"): "Formal complaint sent",
        ("in", "reply"): "Reply received",
        ("in", "bounce"): "Email bounced",
    }
    lines = []
    for row in rows:
        day = clock.to_local_date(datetime.fromisoformat(row["occurred_at"]), jurisdiction.tz)
        label = labels.get((row["direction"], row["kind"]), row["kind"])
        lines.append(f"{format_date(day)}: {label}")
    return lines
