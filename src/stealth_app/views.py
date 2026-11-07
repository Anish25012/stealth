"""What the dashboard shows, in plain English, worked out from the engine's records.

No HTTP here, so the same numbers can feed the monthly report later without drifting from the
dashboard. Broker replies are hostile input: they're turned into plain text and never rendered as
HTML, and evidence paths are checked to stay inside the profile's folder.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from stealth.cases.state import TRANSITIONS, Case, Status, all_cases
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.db import events
from stealth.profiles.store import Profile
from stealth.templates.render import format_date

# Statuses ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusInfo:
    label: str  # short, shown in a badge
    group: str  # which list on the overview it belongs to
    tone: str  # badge style: action, waiting, good, problem or neutral


STATUS: dict[Status, StatusInfo] = {
    Status.DRAFT: StatusInfo("Waiting for your OK", "approve", "action"),
    Status.QUEUED: StatusInfo("Sending soon", "progress", "waiting"),
    Status.AWAITING_FORM: StatusInfo("Web form to fill in", "you", "action"),
    Status.SENT: StatusInfo("Letter sent", "progress", "waiting"),
    Status.REPLIED: StatusInfo("Reply to read", "you", "action"),
    Status.ID_REQUESTED: StatusInfo("They asked for ID", "you", "action"),
    Status.EXTENDED: StatusInfo("They took extra time", "progress", "waiting"),
    Status.REMOVED: StatusInfo("Removed", "done", "good"),
    Status.NO_DATA: StatusInfo("Held nothing", "done", "good"),
    Status.REFUSED: StatusInfo("Refused", "you", "problem"),
    Status.BOUNCED: StatusInfo("Email didn't arrive", "problem", "problem"),
    Status.COMPLAINT_SENT: StatusInfo("Complaint sent", "progress", "waiting"),
    Status.ICO_READY: StatusInfo("Ready for the ICO", "you", "action"),
    Status.ESCALATED: StatusInfo("Reported to the ICO", "progress", "waiting"),
    Status.NEEDS_YOU: StatusInfo("Needs you", "you", "action"),
    Status.SKIPPED: StatusInfo("Skipped", "done", "neutral"),
}

GROUPS: dict[str, tuple[str, str]] = {
    "approve": (
        "Waiting for your OK",
        "Letters Stealth has written. Nothing is sent until you approve.",
    ),
    "you": ("Needs you", "Things only you can do, like reading a reply or filling in a web form."),
    "progress": ("In progress", "Letters sent and waiting for an answer. Stealth keeps track."),
    "problem": ("Problems", "Letters that didn't arrive."),
    "done": ("Done", "Companies that removed you or held nothing. Stealth will check again."),
}


def _on(day: date | None) -> str:
    return format_date(day) if day else "a date not yet known"


# What each status means, with {deadline} and {next} filled in from the case's dates.
EXPLAIN: dict[Status, str] = {
    Status.DRAFT: "Stealth has written this letter. It won't be sent until you approve it.",
    Status.QUEUED: "Approved. It goes out on the next cycle, within your daily sending limit.",
    Status.AWAITING_FORM: (
        "This company only accepts requests through its own web form. Fill it in, then tell "
        "Stealth."
    ),
    Status.SENT: "They must reply by {deadline}.",
    Status.EXTENDED: "They took the extra time the law allows. They must reply by {deadline}.",
    Status.REPLIED: "They replied. Read it and tell Stealth what it said: only you can decide.",
    Status.ID_REQUESTED: (
        "They asked you to prove who you are. Their time limit is paused until you send what "
        "they reasonably need."
    ),
    Status.REMOVED: "They removed you. Companies re-list people, so Stealth asks again on {next}.",
    Status.NO_DATA: "They said they hold nothing about you. Stealth checks again on {next}.",
    Status.REFUSED: "They refused. You can send them a formal complaint, which they must answer.",
    Status.BOUNCED: (
        "The email address Stealth has for them didn't work. When an update to the company list "
        "fixes it, Stealth tries again."
    ),
    Status.COMPLAINT_SENT: (
        "A formal complaint went to them. If it isn't put right by {next}, you can go to the ICO."
    ),
    Status.ICO_READY: (
        "They had their chance. You can now complain to the ICO, the UK's data protection "
        "regulator."
    ),
    Status.ESCALATED: "You've reported them to the ICO. Tell Stealth if they remove you.",
    Status.NEEDS_YOU: "Their time limit has passed and there's a reply nobody has read yet.",
    Status.SKIPPED: "You chose to skip this company.",
}
LATE = "Their reply was due by {deadline}. The next cycle sends them a formal complaint."
FORM_LATE = "Their time limit has passed, and they can only be chased through their website."


def explain(case: Case, today: date) -> str:
    """One sentence on where a case stands and what happens next."""
    text = EXPLAIN[case.status]
    if (
        case.status in (Status.SENT, Status.EXTENDED)
        and case.deadline_on
        and case.deadline_on < today
    ):
        text = LATE
    elif case.status is Status.NEEDS_YOU and (case.method == "form" or not case.contact):
        text = FORM_LATE
    return text.format(deadline=_on(case.deadline_on), next=_on(case.next_action_on))


# Overview ----------------------------------------------------------------------------------


@dataclass
class Row:
    case: Case
    info: StatusInfo
    explanation: str
    overdue: bool


def rows(profile: Profile) -> list[Row]:
    today = today_for(profile)
    result = []
    for case in all_cases(profile.conn):
        overdue = (
            case.status in (Status.SENT, Status.EXTENDED)
            and case.deadline_on is not None
            and case.deadline_on < today
        )
        result.append(Row(case, STATUS[case.status], explain(case, today), overdue))
    return result


def grouped(all_rows: list[Row]) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = {key: [] for key in GROUPS}
    for row in all_rows:
        groups[row.info.group].append(row)
    return groups


def _companies(n: int) -> str:
    return f"{n} compan{'y' if n == 1 else 'ies'}"


@dataclass(frozen=True)
class Summary:
    """The answer to "am I protected, and is there anything I need to do?"."""

    removed: int  # removed, or said they held nothing
    progress: int
    needs: int  # letters to approve plus things only the person can do
    problems: int
    active: int  # every company except skipped ones
    percent: int  # removed as a share of active
    headline: str
    text: str
    tone: str  # "good" when nothing needs the person, otherwise "action"


def summary(groups: dict[str, list[Row]]) -> Summary:
    removed = sum(1 for r in groups["done"] if r.case.status in (Status.REMOVED, Status.NO_DATA))
    skipped = len(groups["done"]) - removed
    needs = len(groups["approve"]) + len(groups["you"])
    problems = len(groups["problem"])
    progress = len(groups["progress"])
    active = sum(len(rows) for rows in groups.values()) - skipped
    percent = round(100 * removed / active) if active else 0
    attention = needs + problems
    gone = f"{_companies(removed)} {'has' if removed == 1 else 'have'} removed your data"
    if not active:
        headline, text = "Nothing to track yet", "Stealth hasn't written to any companies."
    elif attention:
        headline = f"{attention} things need you" if attention > 1 else "1 thing needs you"
        text = f"{gone} so far. Everything else is in hand."
    elif progress:
        headline = "You're protected"
        text = f"{gone}. Stealth is waiting on {progress} more and keeps checking."
    else:
        headline = "You're protected"
        text = f"{gone}. Stealth will ask them again, in case they re-list you."
    return Summary(
        removed,
        progress,
        needs,
        problems,
        active,
        percent,
        headline,
        text,
        "action" if attention else "good",
    )


def nav_counts(groups: dict[str, list[Row]]) -> dict[str, int]:
    """The number beside "Needs you" in the sidebar: everything waiting on the person."""
    return {"/needs": sum(len(groups[key]) for key in ("approve", "you", "problem"))}


def today_for(profile: Profile) -> date:
    from stealth.cases.engine import jurisdiction_of

    return clock.local_date(jurisdiction_of(profile).tz)


# What the person can do on a case ------------------------------------------------------------

# Reply outcomes, in the words a person would use.
OUTCOME_CHOICES: list[tuple[str, Status | None, str]] = [
    ("removed", Status.REMOVED, "They've removed my data"),
    ("no_data", Status.NO_DATA, "They hold no data about me"),
    ("refused", Status.REFUSED, "They refused"),
    ("id_requested", Status.ID_REQUESTED, "They asked for ID or more details"),
    ("extended", Status.EXTENDED, "They said they need more time"),
    ("info_supplied", None, "I've sent them the ID or details they asked for"),
    ("escalated", Status.ESCALATED, "I've complained to the ICO"),
]


def outcomes_for(case: Case) -> list[tuple[str, str]]:
    """The reply outcomes that make sense for this case right now."""
    if case.status in (Status.DRAFT, Status.QUEUED, Status.AWAITING_FORM, Status.SKIPPED):
        return []
    allowed = TRANSITIONS[case.status]
    choices = []
    for value, target, label in OUTCOME_CHOICES:
        if target is None:
            if case.status is Status.ID_REQUESTED:
                choices.append((value, label))
        elif target is Status.ESCALATED:
            if case.status is Status.ICO_READY:
                choices.append((value, label))
        elif target in allowed and target is not case.status:
            choices.append((value, label))
    return choices


def can_skip(case: Case) -> bool:
    return Status.SKIPPED in TRANSITIONS[case.status]


def can_complain(case: Case) -> bool:
    return case.status is Status.REFUSED and bool(case.contact)


# Timeline and evidence -----------------------------------------------------------------------


EVENT_TEXT = {
    "case_created": "Stealth added this company",
    "reply_received": "Stealth found their reply",
    "send_failed": "Sending failed",
    "ico_complaint_drafted": "ICO complaint drafted",
}
MESSAGE_TEXT = {
    ("out", "request"): "Removal request sent",
    ("out", "complaint"): "Formal complaint sent",
    ("in", "reply"): "Reply received",
    ("in", "bounce"): "Email bounced",
}


@dataclass
class Entry:
    at: datetime
    text: str
    detail: str
    who: str
    message_id: int | None = None


def _status_text(payload: dict[str, Any]) -> str:
    try:
        after = STATUS[Status(payload.get("after", ""))].label
    except ValueError:
        after = str(payload.get("after"))
    return f"Now: {after}"


def timeline(profile: Profile, case: Case) -> list[Entry]:
    """Everything that happened to a case, oldest first: the audit log plus every message."""
    entries = []
    for event in events.for_case(profile.conn, case.id):
        payload = event["payload"]
        if event["type"] == "status_changed":
            text, detail = _status_text(payload), str(payload.get("reason", ""))
        else:
            text, detail = (
                EVENT_TEXT.get(event["type"], event["type"]),
                str(payload.get("reason", "")),
            )
        who = "You" if event["actor"] == "you" else "Stealth"
        entries.append(Entry(datetime.fromisoformat(event["at"]), text, detail, who))
    for message in messages(profile, case):
        who = "Stealth" if message["direction"] == "out" else (message["sender"] or "Them")
        entries.append(
            Entry(
                datetime.fromisoformat(message["occurred_at"]),
                MESSAGE_TEXT.get((message["direction"], message["kind"]), message["kind"]),
                message["subject"] or "",
                who,
                message_id=message["id"],
            )
        )
    entries.sort(key=lambda e: e.at)
    return entries


def messages(profile: Profile, case: Case) -> list[dict[str, Any]]:
    rows = profile.conn.execute(
        "SELECT * FROM message WHERE case_id = ? ORDER BY occurred_at, id", (case.id,)
    )
    return [dict(r) for r in rows]


def _evidence_path(profile: Profile, relative: str) -> Path:
    path = (profile.folder / relative).resolve()
    if not path.is_relative_to(profile.folder.resolve()):
        raise ProfileError("That evidence file is outside this profile.")
    return path


class _TextOnly(HTMLParser):
    """Keeps the words of an HTML email and drops everything else, scripts and styles included."""

    SKIP = frozenset({"script", "style", "head", "title"})
    BREAKS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "table"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skipping += 1
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextOnly()
    parser.feed(html)
    lines = (line.strip() for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


@dataclass
class ReadableMessage:
    subject: str
    sender: str
    to: str
    date: str
    body: str
    sha256: str
    verified: bool  # the file still matches the hash recorded when it was saved


def readable(profile: Profile, case: Case, message_id: int) -> ReadableMessage:
    row = profile.conn.execute(
        "SELECT * FROM message WHERE id = ? AND case_id = ?", (message_id, case.id)
    ).fetchone()
    if row is None:
        raise ProfileError("That message isn't part of this case.")
    raw = _evidence_path(profile, row["path"]).read_bytes()
    parsed: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)
    body_part = parsed.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        content = body_part.get_content()
        body = html_to_text(content) if body_part.get_content_type() == "text/html" else content
    return ReadableMessage(
        subject=str(parsed.get("Subject", "")),
        sender=str(parsed.get("From", "")),
        to=str(parsed.get("To", "")),
        date=str(parsed.get("Date", "")),
        body=body.strip() or "(This message has no readable text.)",
        sha256=row["sha256"],
        verified=hashlib.sha256(raw).hexdigest() == row["sha256"],
    )


def latest_reply(profile: Profile, case: Case) -> ReadableMessage | None:
    """The newest reply, shown where the person decides what it meant."""
    row = profile.conn.execute(
        "SELECT id FROM message WHERE case_id = ? AND direction = 'in' AND kind = 'reply' "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (case.id,),
    ).fetchone()
    return readable(profile, case, row["id"]) if row else None


def evidence_pack(profile: Profile, case: Case) -> bytes:
    """A zip of everything about one company: each email as sent or received, and the log."""
    buffer = io.BytesIO()
    entries = timeline(profile, case)
    chain_ok = events.verify_chain(profile.conn)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pack:
        lines = [
            f"Evidence pack: {case.broker_name} ({case.broker_domain})",
            f"Reference: {case.ref}",
            f"Status: {STATUS[case.status].label}",
            f"Exported: {clock.now().isoformat(timespec='seconds')}",
            f"Audit log intact: {'yes' if chain_ok else 'NO - it has been altered'}",
            "",
            "Timeline (UTC):",
        ]
        lines += [
            f"  {e.at.isoformat(timespec='seconds')}  {e.text}"
            + (f" - {e.detail}" if e.detail else "")
            for e in entries
        ]
        lines += ["", "Each .eml file opens in any email program. Its SHA-256 is in manifest.json."]
        pack.writestr("README.txt", "\n".join(lines) + "\n")
        manifest = []
        for message in messages(profile, case):
            path = _evidence_path(profile, message["path"])
            if path.exists():
                pack.write(path, f"emails/{path.name}")
            manifest.append(
                {
                    "file": f"emails/{path.name}",
                    "direction": message["direction"],
                    "kind": message["kind"],
                    "at": message["occurred_at"],
                    "sha256": message["sha256"],
                }
            )
        log_rows = [
            {k: e[k] for k in ("id", "type", "at", "actor", "payload", "prev_hash", "hash")}
            for e in events.for_case(profile.conn, case.id)
        ]
        pack.writestr("manifest.json", json.dumps(manifest, indent=2))
        pack.writestr(
            "audit-log.json", json.dumps({"chain_intact": chain_ok, "events": log_rows}, indent=2)
        )
        draft = profile.evidence / case.broker_id / "ico-complaint-draft.txt"
        if draft.exists():
            pack.write(draft, "ico-complaint-draft.txt")
    return buffer.getvalue()
