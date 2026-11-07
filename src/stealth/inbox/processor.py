"""Reading replies. Read-only on the mailbox, and cautious with what it concludes.

Every reply is read (see `classifier`) and the reading is kept with it. Stealth acts by itself
only when two things are both true: the reading is clear, and the email provably answers our
letter (it's threaded to it, or quotes the case reference). Anyone can put a company's domain in
a From header, so a reply matched only by its sender is never acted on: it waits for a person,
with Stealth's best guess beside it. "We've received your request" and out-of-office replies are
filed on the case without asking anyone to read them.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from stealth.cases.engine import OUTCOMES, apply_outcome, jurisdiction_of, save_message
from stealth.cases.state import TRANSITIONS, Case, Status, all_cases, transition
from stealth.core import clock
from stealth.db import events
from stealth.inbox import classifier
from stealth.inbox.classifier import Classification
from stealth.mail.base import Mailbox
from stealth.profiles.store import Profile
from stealth.registry.loader import Broker

REF_RE = re.compile(r"STL-[0-9A-F]{8}")
BOUNCE_SENDERS = re.compile(r"^(mailer-daemon|postmaster|mail-daemon)@", re.IGNORECASE)
# A DSN status of 5.x.x is permanent. 4.x.x is "still trying", which is not a dead contact.
PERMANENT_STATUS = re.compile(r"^Status:\s*5\.\d+\.\d+", re.IGNORECASE | re.MULTILINE)
FINAL_RECIPIENT = re.compile(r"^Final-Recipient:\s*[^;]*;\s*(\S+)", re.IGNORECASE | re.MULTILINE)

AWAITING_REPLY = {Status.SENT, Status.EXTENDED, Status.ID_REQUESTED, Status.COMPLAINT_SENT}
CAN_BOUNCE = {Status.SENT, Status.COMPLAINT_SENT}
# What Stealth may record by itself from a clear reply. Web forms and "more details" always go
# to the person: they need something only the person can do.
ACTS_ON = (
    classifier.REMOVED,
    classifier.NO_DATA,
    classifier.ID_REQUESTED,
    classifier.EXTENDED,
    classifier.REFUSED,
)


@dataclass
class InboxReport:
    replies: list[str] = field(default_factory=list)  # for a person to read
    bounces: list[str] = field(default_factory=list)
    unmatched: int = 0
    resolved: list[tuple[str, str]] = field(default_factory=list)  # (company, what it meant)
    acknowledged: list[str] = field(default_factory=list)  # filed: nothing to do


def _body_text(message: EmailMessage) -> str:
    """All readable text, including the machine-readable part of a bounce."""
    chunks: list[str] = []
    for part in message.walk():
        kind = part.get_content_type()
        if kind == "message/delivery-status":
            # The parser splits this into header blocks, so join them back into text.
            chunks.extend(str(block) for block in part.get_payload())
        elif kind.startswith("text/"):
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                chunks.append(payload.decode(errors="replace"))
    return "\n".join(chunks)


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


def _domain_matches(sender_domain: str, domains: set[str]) -> bool:
    return any(sender_domain == d or sender_domain.endswith(f".{d}") for d in domains)


class _Matcher:
    """Finds the case a message belongs to, strongest evidence first."""

    def __init__(self, profile: Profile, brokers: dict[str, Broker]) -> None:
        self.cases = all_cases(profile.conn)
        self.by_ref = {c.ref: c for c in self.cases}
        self.by_id = {c.id: c for c in self.cases}
        self.sent_ids = {
            r["rfc_message_id"]: r["case_id"]
            for r in profile.conn.execute(
                "SELECT rfc_message_id, case_id FROM message WHERE direction = 'out'"
            )
        }
        self.domains: dict[int, set[str]] = {}
        for case in self.cases:
            names = {case.broker_domain.lower()}
            if case.contact:
                names.add(_domain(case.contact))
            broker = brokers.get(case.broker_id)
            if broker:
                names.update(broker.all_domains)
            self.domains[case.id] = names

    def match(self, message: EmailMessage, sender: str, text: str) -> tuple[Case | None, bool]:
        """The case, and whether the email provably answers our letter (not just its sender)."""
        threaded = f"{message.get('In-Reply-To', '')} {message.get('References', '')}".split()
        for message_id in threaded:
            if message_id in self.sent_ids:
                return self.by_id[self.sent_ids[message_id]], True
        ref = REF_RE.search(f"{message.get('Subject', '')}\n{text}")
        if ref and ref.group() in self.by_ref:
            return self.by_ref[ref.group()], True
        sender_domain = _domain(sender)
        hits = [c for c in self.cases if _domain_matches(sender_domain, self.domains[c.id])]
        return (hits[0] if len(hits) == 1 else None), False

    def match_bounce(self, text: str) -> Case | None:
        ref = REF_RE.search(text)
        if ref and ref.group() in self.by_ref:
            return self.by_ref[ref.group()]
        recipient = FINAL_RECIPIENT.search(text)
        target = recipient.group(1).lower() if recipient else ""
        for case in self.cases:
            if case.contact and case.contact.lower() == target:
                return case
        return None


def _is_bounce(message: EmailMessage, sender: str) -> bool:
    return (
        message.get_content_type() == "multipart/report"
        and message.get_param("report-type") == "delivery-status"
    ) or bool(BOUNCE_SENDERS.match(sender))


def _seen(profile: Profile, message_id: str) -> bool:
    row = profile.conn.execute(
        "SELECT 1 FROM message WHERE rfc_message_id = ?", (message_id,)
    ).fetchone()
    return row is not None


def _flag_contact(profile: Profile, case: Case) -> None:
    profile.conn.execute(
        "INSERT OR REPLACE INTO broker_contact_flag (broker_id, contact, reason, flagged_at) "
        "VALUES (?, ?, 'bounced', ?)",
        (case.broker_id, case.contact or "", clock.now().isoformat()),
    )


def _earliest_date(profile: Profile, today: date) -> date:
    row = profile.conn.execute("SELECT min(created_at) AS first FROM cases").fetchone()
    if not row["first"]:
        return today
    return datetime.fromisoformat(row["first"]).date() - timedelta(days=1)


def process_inbox(profile: Profile, mailbox: Mailbox, brokers: dict[str, Broker]) -> InboxReport:
    """Match new mail to cases. Safe to run repeatedly: each message is handled once."""
    jurisdiction = jurisdiction_of(profile)
    account = profile.mail_account()
    own = account.address.lower() if account else ""
    today = clock.local_date(jurisdiction.tz)
    matcher = _Matcher(profile, brokers)
    report = InboxReport()

    for item in mailbox.fetch_since(_earliest_date(profile, today)):
        message = email.message_from_bytes(item.raw, policy=email.policy.default)
        message_id = str(message.get("Message-ID") or hashlib.sha256(item.raw).hexdigest())
        if _seen(profile, message_id):
            continue
        sender = email.utils.parseaddr(str(message.get("From", "")))[1].lower()
        text = _body_text(message)

        if _is_bounce(message, sender):
            case = matcher.match_bounce(text)
            if case and PERMANENT_STATUS.search(text):
                _record(profile, case, item.raw, message_id, sender, message, "bounce")
                _flag_contact(profile, case)
                if case.status in CAN_BOUNCE:
                    transition(profile.conn, case, Status.BOUNCED, reason="email bounced")
                report.bounces.append(case.broker_name)
            continue

        if sender == own:
            continue  # our own BCC copy
        case, proven = matcher.match(message, sender, text)
        if case is None:
            report.unmatched += 1
            continue
        reading = classifier.classify(str(message.get("Subject", "")), text, message)
        _record(profile, case, item.raw, message_id, sender, message, "reply", reading)
        events.append(
            profile.conn,
            "reply_received",
            case_id=case.id,
            sender=sender,
            reading=reading.kind,
            confident=reading.confident,
        )
        _act_on(profile, case, reading, proven, report)
    return report


def _act_on(
    profile: Profile, case: Case, reading: Classification, proven: bool, report: InboxReport
) -> None:
    if reading.needs_nothing and case.status in AWAITING_REPLY:
        report.acknowledged.append(case.broker_name)  # filed on the case; the clock keeps running
        return
    if proven and reading.confident and reading.kind in ACTS_ON and _can_apply(case, reading):
        apply_outcome(
            profile, case, reading.kind, actor="stealth", reason=f"reply read: {reading.label}"
        )
        report.resolved.append((case.broker_name, reading.label))
        return
    if case.status in AWAITING_REPLY:
        transition(profile.conn, case, Status.REPLIED, reason="reply received")
    report.replies.append(case.broker_name)


def _can_apply(case: Case, reading: Classification) -> bool:
    target = OUTCOMES[reading.kind]
    if target is Status.EXTENDED and case.relevant_time is None:
        return False
    if target is Status.REFUSED and case.complaint_on:
        return False  # refused after a complaint means the ICO: a person takes that step
    if target in (Status.REMOVED, Status.NO_DATA) and case.last_request_on is None:
        return False
    return target in TRANSITIONS[case.status] and target is not case.status


def _record(
    profile: Profile,
    case: Case,
    raw: bytes,
    message_id: str,
    sender: str,
    message: EmailMessage,
    kind: str,
    reading: Classification | None = None,
) -> None:
    save_message(
        profile,
        case,
        direction="in",
        kind=kind,
        raw=raw,
        rfc_message_id=message_id,
        sender=sender,
        subject=str(message.get("Subject", "")),
        classification=json.dumps(_stored(reading)) if reading else None,
    )


def _stored(reading: Classification) -> dict[str, object]:
    return {
        "kind": reading.kind,
        "confident": reading.confident,
        "quote": reading.quote,
        "urls": list(reading.urls),
    }
