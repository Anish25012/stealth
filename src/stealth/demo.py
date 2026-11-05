"""A full fake cycle: real code, local mail servers, made-up companies, time on fast-forward.

Everything happens in a temporary folder with fake people and fake `.test` addresses, so no email
can leave this computer. Each stage is checked, so a passing demo really means the pipeline works.
"""

from __future__ import annotations

import email
import email.policy
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from stealth import cycle
from stealth.cases import engine
from stealth.cases.state import Status, all_cases
from stealth.core import clock
from stealth.core.errors import StealthError
from stealth.core.secrets import MemoryStore
from stealth.db import events
from stealth.devmail.servers import Delivered, LocalImap, LocalSmtp, bounce_of, reply_to
from stealth.inbox.processor import process_inbox
from stealth.mail.smtp_imap import ImapMailbox, SmtpTransport
from stealth.profiles.models import MailAccount, Person
from stealth.profiles.store import Profile, ProfileStore
from stealth.registry.loader import Broker

PASSWORD = "demo-only"  # noqa: S105 - for a local throwaway server
ADDRESS = "alex.example@stealth-demo.test"


def _broker(
    id_: str, name: str, *, category: str = "data-aggregator", form: bool = False
) -> Broker:
    domain = f"{id_}.test"
    return Broker(
        id=id_,
        name=name,
        domain=domain,
        region="UK",
        category=category,
        method="form" if form else "email",
        email=None if form else f"privacy@{domain}",
        form_url=f"https://{domain}/remove" if form else None,
        notes="",
        source="demo",
        last_verified="2026-11-01",
        laws=("uk-gdpr",),
        tier="core",
        confidence=1.0,
    )


DEMO_BROKERS = {
    b.id: b
    for b in [
        _broker("prompt-data", "Prompt Data Ltd"),
        _broker("silent-lists", "Silent Lists Ltd"),
        _broker("refusing-corp", "Refusing Corp"),
        _broker("dead-address", "Dead Address Ltd"),
        _broker("find-a-neighbour", "Find-a-Neighbour", category="people-search"),
        _broker("form-only", "Form Only Ltd", form=True),
    ]
}


@dataclass
class DemoResult:
    statuses: dict[str, str]
    emails_sent: int
    chain_ok: bool
    ico_draft: str


class DemoFailure(StealthError):
    """A demo stage did not end up where it should have."""


def _expect(actual: object, expected: object, what: str) -> None:
    if actual != expected:
        raise DemoFailure(f"Demo check failed: {what}. Expected {expected}, got {actual}.")


def demo_account(smtp: LocalSmtp, imap: LocalImap) -> MailAccount:
    return MailAccount(
        provider="demo",
        address=ADDRESS,
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_security="none",
        imap_host="127.0.0.1",
        imap_port=imap.port,
        imap_security="none",
        username=ADDRESS,
    )


def _sent_to(smtp: LocalSmtp, address: str) -> Delivered:
    return next(d for d in reversed(smtp.delivered) if address in d.recipients)


def _set_day(day: date) -> None:
    clock.set_time(datetime(day.year, day.month, day.day, 9, tzinfo=UTC))


def _statuses(profile: Profile) -> dict[str, str]:
    return {c.broker_id: c.status.value for c in all_cases(profile.conn)}


def run_demo(say: Callable[[str], None], start: date = date(2026, 11, 2)) -> DemoResult:
    """Run the whole story, narrating through `say`. Raises DemoFailure if anything is wrong."""
    with (
        tempfile.TemporaryDirectory(prefix="stealth-demo-", ignore_cleanup_errors=True) as tmp,
        LocalSmtp() as smtp,
        LocalImap(PASSWORD) as imap,
    ):
        store = ProfileStore(MemoryStore(), home=Path(tmp))
        profile_id, _ = store.create()
        account = demo_account(smtp, imap)
        try:
            with store.open(profile_id) as profile:
                return _story(profile, account, smtp, imap, say, start)
        finally:
            clock.reset()


def _set_up(profile: Profile, account: MailAccount) -> None:
    profile.save_person(
        Person(
            display_name="Alex Example",
            names=["Alex Example"],
            emails=[ADDRESS],
            addresses=["1 Demo Street, Testville"],
            phones=[],
        )
    )
    profile.save_mail_account(account, password=PASSWORD)
    profile.save_settings(send_delay_seconds=0)


def _story(
    profile: Profile,
    account: MailAccount,
    smtp: LocalSmtp,
    imap: LocalImap,
    say: Callable[[str], None],
    start: date,
) -> DemoResult:
    _set_up(profile, account)
    transport = SmtpTransport(account, PASSWORD)
    mailbox = ImapMailbox(account, PASSWORD)
    no_sleep = lambda _seconds: None  # noqa: E731

    def run() -> cycle.CycleReport:
        return cycle.run_cycle(profile, DEMO_BROKERS, transport, mailbox, sleep=no_sleep)

    _set_day(start)
    say(f"[{start}] A throwaway profile for Alex Example, and {len(DEMO_BROKERS)} fake companies.")

    say("Step 1: dry run. This writes .eml files and sends nothing.")
    preview = cycle.preview_cycle(profile, DEMO_BROKERS)
    _expect(len(preview.send.written), 5, "one .eml per email company")
    _expect(len(smtp.delivered), 0, "a dry run sends nothing")
    _expect(all_cases(profile.conn), [], "a dry run creates no cases")
    say(f"  {len(preview.send.written)} letters previewed, nothing sent.")

    say("Step 2: first live cycle.")
    report = run()
    _expect(len(report.send.sent), 5, "five requests sent")
    _expect(report.send.forms, ["Form Only Ltd"], "form-only company flagged")
    say(f"  {len(report.send.sent)} requests sent, 1 company needs a web form.")
    engine.form_done(profile, "form-only")
    say("  You filled in Form Only Ltd's web form, so its clock has started.")

    _set_day(start + timedelta(days=5))
    day = datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(days=5)
    say(f"[{clock.local_date('UTC')}] Companies reply; one address turns out to be dead.")
    for address, text in (
        ("privacy@prompt-data.test", "Your data has been erased."),
        ("privacy@find-a-neighbour.test", "We hold no data about you."),
        ("privacy@refusing-corp.test", "We decline your request."),
    ):
        imap.add(reply_to(_sent_to(smtp, address).data, address, text, day=day))
    dead = "privacy@dead-address.test"
    imap.add(bounce_of(_sent_to(smtp, dead).data, dead, day=day))
    report = run()
    _expect(len(report.inbox.replies), 3, "three replies matched")
    _expect(report.inbox.bounces, ["Dead Address Ltd"], "bounce detected")
    say("  3 replies matched and marked 'replied'. Only you can confirm 'removed'.")
    engine.confirm(profile, "prompt-data", "removed")
    engine.confirm(profile, "find-a-neighbour", "no_data")
    engine.confirm(profile, "refusing-corp", "refused")
    say("  You read them and confirmed: removed, no data, refused.")
    case = next(c for c in all_cases(profile.conn) if c.broker_id == "refusing-corp")
    engine.send_complaint(profile, case, transport, grievance=engine.REFUSED)
    say("  You sent Refusing Corp a formal complaint.")

    deadline_passed = start + timedelta(days=32)
    _set_day(deadline_passed)
    say(f"[{deadline_passed}] One month is up. Silent Lists Ltd never replied.")
    report = run()
    _expect(report.complaints, ["Silent Lists Ltd"], "overdue company gets a formal complaint")
    _expect(report.needs_you, ["Form Only Ltd"], "form company with no email goes to you")
    say("  Silent Lists gets a formal complaint. Form Only Ltd goes to your 'needs you' list.")

    reconsider = deadline_passed + timedelta(days=1)
    _set_day(reconsider)
    imap.add(
        reply_to(
            _sent_to(smtp, "privacy@refusing-corp.test").data,
            "privacy@refusing-corp.test",
            "We still decline.",
            day=datetime(reconsider.year, reconsider.month, reconsider.day, tzinfo=UTC),
        )
    )
    run()
    engine.confirm(profile, "refusing-corp", "refused")
    say(f"[{reconsider}] Refusing Corp refuses again, so it's ICO-ready straight away.")
    draft = engine.draft_ico_complaint(profile, "refusing-corp")

    ico_day = deadline_passed + timedelta(days=30)
    _set_day(ico_day)
    report = run()
    _expect(report.ico_ready, ["Silent Lists Ltd"], "ICO-ready 30 days after the complaint")
    say(f"[{ico_day}] 30 days after its complaint, Silent Lists is ICO-ready too.")
    _expect(report.resends, ["Find-a-Neighbour"], "people-search repeat after 60 days")
    say("  Brokers re-list people, so the people-search site got its 60-day repeat request.")

    resend_day = start + timedelta(days=90)
    _set_day(resend_day)
    report = run()
    _expect(report.resends, ["Prompt Data Ltd"], "other companies repeat after 90 days")
    say(f"[{resend_day}] Other companies get their repeat request after 90 days.")

    chain_ok = events.verify_chain(profile.conn)
    _expect(chain_ok, True, "the audit log is intact")
    statuses = _statuses(profile)
    _expect(statuses["silent-lists"], Status.ICO_READY.value, "Silent Lists status")
    _expect(statuses["refusing-corp"], Status.ICO_READY.value, "Refusing Corp status")
    _expect(statuses["dead-address"], Status.BOUNCED.value, "Dead Address status")
    _expect(statuses["prompt-data"], Status.SENT.value, "Prompt Data re-sent")

    parsed = email.message_from_bytes(smtp.delivered[0].data, policy=email.policy.default)
    _expect(parsed.get("Bcc"), None, "BCC is never visible to the recipient")
    text = draft.read_text("utf-8")
    say(f"Audit log intact. {len(smtp.delivered)} emails delivered to the local test server.")
    return DemoResult(statuses, len(smtp.delivered), chain_ok, text)


# A demo world for the dashboard ------------------------------------------------------------

# Companies the dashboard demo adds after the story's first round, so there's always something
# arriving later: one that replies but hasn't been read, and two letters waiting for approval.
LATER_BROKERS = {
    b.id: b
    for b in [
        _broker("chatty-data", "Chatty Data Ltd"),
        _broker("new-lists", "New Lists Ltd"),
        _broker("people-finder", "People Finder UK", category="people-search"),
    ]
}
DASHBOARD_BROKERS = {**DEMO_BROKERS, **LATER_BROKERS}
DRAFTS = ("new-lists", "people-finder")


def _morning(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, tzinfo=UTC)


def seed_dashboard_demo(store: ProfileStore, smtp: LocalSmtp, imap: LocalImap, today: date) -> str:
    """Create a demo profile part-way through its story and return its id.

    The history is back-dated so the dashboard shows every kind of case at once, relative to the
    real today: removed, nothing held, refused, bounced, an unread reply, a web form, a company
    past its deadline (the next cycle sends it a complaint) and two letters waiting for approval.
    The clock is frozen only while history is written, then left at real time.
    """
    profile_id, _ = store.create()
    account = demo_account(smtp, imap)
    start = today - timedelta(days=33)
    first_round = {k: v for k, v in DASHBOARD_BROKERS.items() if k in DEMO_BROKERS}
    no_sleep = lambda _seconds: None  # noqa: E731
    with store.open(profile_id) as profile:
        _set_up(profile, account)
        transport, mailbox = SmtpTransport(account, PASSWORD), ImapMailbox(account, PASSWORD)

        def run(brokers: dict[str, Broker]) -> None:
            cycle.run_cycle(profile, brokers, transport, mailbox, sleep=no_sleep)

        with clock.frozen_at(_morning(start)):
            run(first_round)
            engine.form_done(profile, "form-only")
        replied = start + timedelta(days=5)
        with clock.frozen_at(_morning(replied)):
            for address, text in (
                ("privacy@prompt-data.test", "Your data has been erased."),
                ("privacy@find-a-neighbour.test", "We hold no data about you."),
                ("privacy@refusing-corp.test", "We decline your request."),
            ):
                imap.add(
                    reply_to(_sent_to(smtp, address).data, address, text, day=_morning(replied))
                )
            dead = "privacy@dead-address.test"
            imap.add(bounce_of(_sent_to(smtp, dead).data, dead, day=_morning(replied)))
            run(first_round)
            engine.confirm(profile, "prompt-data", "removed")
            engine.confirm(profile, "find-a-neighbour", "no_data")
            engine.confirm(profile, "refusing-corp", "refused")
        later = start + timedelta(days=12)
        with clock.frozen_at(_morning(later)):
            engine.send_requests(
                profile, {"chatty-data": LATER_BROKERS["chatty-data"]}, transport, sleep=no_sleep
            )
        yesterday = today - timedelta(days=1)
        with clock.frozen_at(_morning(today)):
            chatty = "privacy@chatty-data.test"
            imap.add(
                reply_to(
                    _sent_to(smtp, chatty).data,
                    chatty,
                    "Thanks for your email. We are looking into this and will be in touch.",
                    day=_morning(yesterday),
                )
            )
            process_inbox(profile, mailbox, DASHBOARD_BROKERS)
            engine.send_requests(
                profile, DASHBOARD_BROKERS, transport, sleep=no_sleep, hold_for_approval=True
            )
    return profile_id
