"""One full pass: replies, then follow-ups, then new and repeat requests.

The order matters. Reading replies first means nobody gets a complaint for a letter they answered
yesterday.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from stealth import autopilot, followups
from stealth.cases import engine
from stealth.cases.engine import NO_REPLY, Sleep, jurisdiction_of
from stealth.cases.state import Case, Status, all_cases, find_case, transition
from stealth.core import clock
from stealth.core.errors import MailSendError
from stealth.custom import store as custom
from stealth.inbox.processor import InboxReport, process_inbox
from stealth.mail.base import Mailbox, Transport
from stealth.profiles.store import Profile
from stealth.registry.loader import Broker


@dataclass
class CycleReport:
    inbox: InboxReport = field(default_factory=InboxReport)
    complaints: list[str] = field(default_factory=list)
    complaints_held: int = 0
    complaints_waiting: list[str] = field(default_factory=list)  # due, waiting for your OK
    autopilot_sent: list[str] = field(default_factory=list)  # requests Autopilot sent
    followups: list[str] = field(default_factory=list)  # Autopilot answered or chased them
    followups_failed: list[str] = field(default_factory=list)
    ico_ready: list[str] = field(default_factory=list)
    needs_you: list[str] = field(default_factory=list)
    resends: list[str] = field(default_factory=list)
    send: engine.SendReport = field(default_factory=engine.SendReport)


def _overdue(case: Case, today: date) -> bool:
    return case.deadline_on is not None and case.deadline_on < today


def _complaint_due(case: Case, today: date) -> bool:
    return case.status in (Status.SENT, Status.EXTENDED) and _overdue(case, today)


def complaints_awaiting_approval(profile: Profile) -> list[Case]:
    """Companies past their deadline that a complaint can go to, oldest deadline first."""
    today = clock.local_date(jurisdiction_of(profile).tz)
    due = [c for c in all_cases(profile.conn) if _complaint_due(c, today) and c.contact]
    return sorted(due, key=lambda c: (c.deadline_on or today, c.broker_name))


def _due(case: Case, today: date) -> bool:
    return case.next_action_on is not None and case.next_action_on <= today


def _update_states(
    profile: Profile, brokers: dict[str, Broker], report: CycleReport, *, hold: bool
) -> None:
    """Moves that need no email. With `hold`, letters due again wait for approval as drafts."""
    today = clock.local_date(jurisdiction_of(profile).tz)
    conn = profile.conn
    for case in all_cases(conn):
        if case.status is Status.REPLIED and _overdue(case, today):
            # They answered, but nobody has said what it meant. A complaint might be wrong.
            transition(conn, case, Status.NEEDS_YOU, reason="reply needs reading, deadline passed")
            report.needs_you.append(case.broker_name)
        elif _complaint_due(case, today) and not case.contact:
            # A web-form company with no email: only a person can chase it.
            transition(conn, case, Status.NEEDS_YOU, reason="deadline passed, no email address")
            report.needs_you.append(case.broker_name)
        elif case.status is Status.COMPLAINT_SENT and _due(case, today):
            transition(conn, case, Status.ICO_READY, reason="complaint period ended")
            report.ico_ready.append(case.broker_name)
        elif case.status in (Status.REMOVED, Status.NO_DATA) and _due(case, today):
            target = Status.AWAITING_FORM if case.method == "form" else _ready(hold)
            transition(conn, case, target, reason="repeat request due", next_action_on=None)
            report.resends.append(case.broker_name)
        elif case.status is Status.BOUNCED:
            _retry_if_contact_fixed(profile, case, brokers, hold=hold)


def _ready(hold: bool) -> Status:
    return Status.DRAFT if hold else Status.QUEUED


def _retry_if_contact_fixed(
    profile: Profile, case: Case, brokers: dict[str, Broker], *, hold: bool
) -> None:
    """A registry update may have replaced a dead address, which is how fixes reach everyone."""
    broker = brokers.get(case.broker_id)
    if broker and broker.email and broker.email.lower() != (case.contact or "").lower():
        transition(
            profile.conn,
            case,
            _ready(hold),
            reason="new contact in broker list",
            contact=broker.email,
        )


def _send_complaints(
    profile: Profile, transport: Transport, sleep: Sleep, report: CycleReport
) -> None:
    """Complaints for missed deadlines: sent only if the person turned that on.

    Otherwise they wait for the person's OK (`stealth complain`, or the button in the app).
    """
    jurisdiction = jurisdiction_of(profile)
    settings = profile.settings()
    due = complaints_awaiting_approval(profile)
    if not autopilot.complaints_automatic(settings):
        report.complaints_waiting = [c.broker_name for c in due]
        return
    on_autopilot = autopilot.is_on(settings)
    if on_autopilot:
        # A refusal gets the same formal complaint, with the refusal as the grievance.
        refused = [c for c in all_cases(profile.conn) if c.status is Status.REFUSED and c.contact]
        due = refused + due
    remaining = max(0, settings.daily_send_limit - engine.sent_today(profile, jurisdiction))
    report.complaints_held = max(0, len(due) - remaining)
    for index, case in enumerate(due[:remaining]):
        if index:
            sleep(settings.send_delay_seconds)
        grievance = engine.REFUSED if case.status is Status.REFUSED else NO_REPLY
        actor = autopilot.ACTOR if on_autopilot else "stealth"
        try:
            sent = engine.send_complaint(profile, case, transport, grievance=grievance, actor=actor)
        except MailSendError:
            report.complaints_held += 1
            continue
        report.complaints.append(case.broker_name)
        if on_autopilot:
            reason = "they refused" if grievance == engine.REFUSED else "no reply by the deadline"
            autopilot.record(profile, sent, "complaint", reason)


def run_cycle(
    profile: Profile,
    brokers: dict[str, Broker],
    transport: Transport,
    mailbox: Mailbox,
    *,
    sleep: Sleep = time.sleep,
    hold_for_approval: bool = False,
) -> CycleReport:
    """A live cycle. See `engine.send_requests` for what `hold_for_approval` changes."""
    report = CycleReport()
    brokers = custom.with_custom(profile, brokers)
    on_autopilot = autopilot.is_on(profile.settings())
    report.inbox = process_inbox(profile, mailbox, brokers, follow_links=on_autopilot)
    if on_autopilot:
        report.followups, report.followups_failed = followups.send_due(profile, transport, sleep)
    _update_states(profile, brokers, report, hold=hold_for_approval)
    _send_complaints(profile, transport, sleep, report)
    approved = _autopilot_batch(profile, brokers) if hold_for_approval else []
    report.send = engine.send_requests(
        profile, brokers, transport, sleep=sleep, hold_for_approval=hold_for_approval
    )
    for case in approved:
        sent = find_case(profile.conn, case.broker_id)
        if sent.status is Status.SENT and sent.last_request_on:
            what = "repeat" if sent.round > 1 else "request"
            autopilot.record(profile, sent, what, f"sent to {sent.contact}")
            report.autopilot_sent.append(sent.broker_name)
    return report


def _autopilot_batch(profile: Profile, brokers: dict[str, Broker]) -> list[Case]:
    """On Autopilot, approve today's batch of core drafts: as many as today's limit has room for.

    Only that many, so turning Autopilot off leaves the rest waiting rather than already queued.
    """
    settings = profile.settings()
    jurisdiction = jurisdiction_of(profile)
    if not autopilot.sends_letters(settings, clock.local_date(jurisdiction.tz)):
        return []  # off, or before the start date the person chose
    engine.open_cases(profile, brokers, hold=True)
    queued = sum(1 for c in all_cases(profile.conn) if c.status is Status.QUEUED)
    room = settings.daily_send_limit - engine.sent_today(profile, jurisdiction) - queued
    drafts = [c for c in engine.drafts_waiting(profile) if autopilot.handles_draft(settings, c)]
    batch = drafts[: max(0, room)]
    engine.approve(
        profile, [c.broker_id for c in batch], actor=autopilot.ACTOR, reason="approved by Autopilot"
    )
    return batch


@dataclass
class CyclePreview:
    complaints: list[Path] = field(default_factory=list)
    complaints_waiting: list[str] = field(default_factory=list)
    send: engine.SendReport = field(default_factory=engine.SendReport)


def preview_cycle(profile: Profile, brokers: dict[str, Broker]) -> CyclePreview:
    """What a cycle would send, as `.eml` files. Touches no mailbox and no case."""
    jurisdiction = jurisdiction_of(profile)
    person, account = profile.person(), profile.mail_account()
    preview = CyclePreview(send=engine.preview_requests(profile, brokers))
    assert account
    due = complaints_awaiting_approval(profile)
    if not profile.settings().auto_complaints:
        preview.complaints_waiting = [c.broker_name for c in due]
        return preview
    for case in due:
        message: EmailMessage = engine.complaint_message(
            person,
            account,
            jurisdiction,
            case,
            grievance=NO_REPLY,
            details=engine.details_for(profile, case, brokers),
        )
        preview.complaints.append(
            engine.write_dry_run(profile, case.broker_id, message, "complaint")
        )
    return preview
