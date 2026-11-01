"""The whole life of a case, driven by moving the clock against real local mail servers."""

import email
import email.policy
from datetime import UTC, date, datetime, timedelta

import pytest

from stealth import cycle
from stealth.cases import engine
from stealth.cases.state import Status, all_cases, find_case
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.db import events
from stealth.devmail.servers import LocalImap, LocalSmtp, bounce_of, reply_to
from stealth.inbox.processor import process_inbox
from stealth.mail.smtp_imap import ImapMailbox, SmtpTransport
from stealth.profiles.models import MailAccount
from tests.helpers import email_broker, form_broker, registry

START = date(2026, 1, 15)
NO_SLEEP = lambda _s: None  # noqa: E731


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 10, tzinfo=UTC)


def go_to(day: date) -> None:
    clock.set_time(at(day))


class World:
    """A profile wired to local mail servers."""

    def __init__(self, profile, smtp, imap, brokers):
        self.profile, self.smtp, self.imap, self.brokers = profile, smtp, imap, brokers
        account = MailAccount(
            provider="smtp",
            address="test.person@example.org",
            smtp_host="127.0.0.1",
            smtp_port=smtp.port,
            smtp_security="none",
            imap_host="127.0.0.1",
            imap_port=imap.port,
            imap_security="none",
            username="test.person@example.org",
        )
        profile.save_mail_account(account, password="pw")
        profile.save_settings(send_delay_seconds=0)
        self.transport = SmtpTransport(account, "pw")
        self.mailbox = ImapMailbox(account, "pw")

    def cycle(self):
        return cycle.run_cycle(
            self.profile, self.brokers, self.transport, self.mailbox, sleep=NO_SLEEP
        )

    def case(self, broker="acme"):
        return find_case(self.profile.conn, broker)

    def sent_to(self, address):
        return next(d for d in reversed(self.smtp.delivered) if address in d.recipients)

    def reply(self, broker="acme", text="Thanks", day=None):
        address = f"privacy@{broker}.example"
        self.imap.add(reply_to(self.sent_to(address).data, address, text, day=at(day or START)))

    def bodies_to(self, address):
        return [
            d.message.get_body().get_content()
            for d in self.smtp.delivered
            if address in d.recipients
        ]


@pytest.fixture
def world(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        yield World(profile, smtp, imap, registry(email_broker()))


def test_sent_case_starts_the_clock(world):
    world.cycle()
    case = world.case()
    assert case.status is Status.SENT
    assert case.relevant_time == START
    assert case.deadline_on == date(2026, 2, 15)
    assert case.round == 1


def test_nothing_happens_on_the_deadline_day_itself(world):
    world.cycle()
    go_to(date(2026, 2, 15))
    assert world.cycle().complaints == []
    assert world.case().status is Status.SENT


def test_complaint_goes_out_the_day_after_the_deadline(world):
    world.cycle()
    go_to(date(2026, 2, 16))
    assert world.cycle().complaints == ["acme Ltd"]
    case = world.case()
    assert case.status is Status.COMPLAINT_SENT
    assert case.complaint_on == date(2026, 2, 16)
    body = " ".join(world.bodies_to("privacy@acme.example")[-1].split())
    assert "section 164A" in body
    assert "15 January 2026" in body
    assert "within 30 days" in body


def test_ico_ready_after_30_days_not_14(world):
    world.cycle()
    go_to(date(2026, 2, 16))
    world.cycle()
    go_to(date(2026, 3, 2))  # 14 days on
    world.cycle()
    assert world.case().status is Status.COMPLAINT_SENT
    go_to(date(2026, 3, 18))  # 30 days on
    assert world.cycle().ico_ready == ["acme Ltd"]
    assert world.case().status is Status.ICO_READY


def test_ico_days_are_configurable(world):
    world.profile.save_settings(ico_ready_days=14)
    world.cycle()
    go_to(date(2026, 2, 16))
    world.cycle()
    go_to(date(2026, 3, 2))
    world.cycle()
    assert world.case().status is Status.ICO_READY


def test_reply_marks_replied_but_never_removed(world):
    world.cycle()
    world.reply(text="Your data has been removed.")
    report = process_inbox(world.profile, world.mailbox, world.brokers)
    assert report.replies == ["acme Ltd"]
    assert world.case().status is Status.REPLIED


def test_inbox_handles_each_message_once(world):
    world.cycle()
    world.reply()
    process_inbox(world.profile, world.mailbox, world.brokers)
    assert process_inbox(world.profile, world.mailbox, world.brokers).replies == []
    count = world.profile.conn.execute(
        "SELECT count(*) FROM message WHERE direction = 'in'"
    ).fetchone()[0]
    assert count == 1


def test_replied_case_past_deadline_asks_the_person_rather_than_complaining(world):
    world.cycle()
    world.reply()
    go_to(date(2026, 1, 20))
    world.cycle()
    go_to(date(2026, 2, 16))
    report = world.cycle()
    assert report.complaints == []
    assert report.needs_you == ["acme Ltd"]
    assert world.case().status is Status.NEEDS_YOU


def test_a_reply_read_first_prevents_a_complaint(world):
    """The cycle reads mail before complaining, whatever day the reply lands."""
    world.cycle()
    go_to(date(2026, 2, 16))
    world.reply(day=date(2026, 2, 16))
    report = world.cycle()
    assert report.complaints == []
    assert world.case().status is Status.NEEDS_YOU


def test_removed_then_resent_after_interval(world):
    world.cycle()
    world.reply()
    world.cycle()
    assert engine.confirm(world.profile, "acme", "removed").status is Status.REMOVED
    assert world.case().next_action_on == START + timedelta(days=90)
    go_to(START + timedelta(days=89))
    assert world.cycle().resends == []
    go_to(START + timedelta(days=90))
    report = world.cycle()
    assert report.resends == ["acme Ltd"]
    case = world.case()
    assert case.status is Status.SENT
    assert case.round == 2
    assert case.relevant_time == START + timedelta(days=90)


def test_people_search_resends_after_60_days(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        brokers = registry(email_broker("finder", category="people-search"))
        w = World(profile, smtp, imap, brokers)
        w.cycle()
        engine.confirm(profile, "finder", "no_data")
        go_to(START + timedelta(days=60))
        assert w.cycle().resends == ["finder Ltd"]


def test_resend_clears_old_complaint_date(world):
    world.cycle()
    go_to(date(2026, 2, 16))
    world.cycle()
    engine.confirm(world.profile, "acme", "removed")
    go_to(START + timedelta(days=90))
    world.cycle()
    assert world.case().complaint_on is None


def test_id_request_stops_the_clock_and_supplying_it_restarts_it(world):
    """Art 12A: time runs from when the company receives what it reasonably asked for."""
    world.cycle()
    engine.confirm(world.profile, "acme", "id_requested")
    assert world.case().deadline_on is None
    go_to(date(2026, 6, 1))
    assert world.cycle().complaints == []  # no deadline while it's stopped
    case = engine.confirm(world.profile, "acme", "info_supplied")
    assert case.status is Status.SENT
    assert case.relevant_time == date(2026, 6, 1)
    assert case.deadline_on == date(2026, 7, 1)


def test_extension_moves_the_deadline_two_months(world):
    world.cycle()
    case = engine.confirm(world.profile, "acme", "extended")
    assert case.deadline_on == date(2026, 4, 15)
    go_to(date(2026, 4, 15))
    assert world.cycle().complaints == []
    go_to(date(2026, 4, 16))
    assert world.cycle().complaints == ["acme Ltd"]


def test_refusal_after_complaint_is_ico_ready_immediately(world):
    world.cycle()
    engine.confirm(world.profile, "acme", "refused")
    engine.send_complaint(world.profile, world.case(), world.transport, grievance=engine.REFUSED)
    assert "refused" in world.bodies_to("privacy@acme.example")[-1]
    assert engine.confirm(world.profile, "acme", "refused").status is Status.ICO_READY


def test_ico_draft_refused_until_ready_and_says_when(world):
    world.cycle()
    go_to(date(2026, 2, 16))
    world.cycle()
    with pytest.raises(ProfileError, match="18 March 2026"):
        engine.draft_ico_complaint(world.profile, "acme")


def test_ico_draft_contains_the_facts(world):
    world.cycle()
    go_to(date(2026, 2, 16))
    world.cycle()
    go_to(date(2026, 3, 18))
    world.cycle()
    path = engine.draft_ico_complaint(world.profile, "acme")
    text = path.read_text("utf-8")
    assert "acme Ltd" in text
    assert "15 January 2026" in text
    assert "16 February 2026" in text
    assert "section 164A" in text
    assert "Erasure request sent" in text
    assert "Formal complaint sent" in text
    assert "I did not receive a substantive response" in text
    assert "DRAFT" in text
    engine.confirm(world.profile, "acme", "escalated")
    assert world.case().status is Status.ESCALATED


def test_bounce_flags_the_contact_and_stops_chasing(world):
    world.cycle()
    address = "privacy@acme.example"
    world.imap.add(bounce_of(world.sent_to(address).data, address, day=at(START)))
    report = process_inbox(world.profile, world.mailbox, world.brokers)
    assert report.bounces == ["acme Ltd"]
    assert world.case().status is Status.BOUNCED
    flag = world.profile.conn.execute("SELECT * FROM broker_contact_flag").fetchone()
    assert (flag["contact"], flag["reason"]) == (address, "bounced")
    go_to(date(2026, 3, 1))
    assert world.cycle().complaints == []


def test_temporary_delay_is_not_a_bounce(world):
    world.cycle()
    address = "privacy@acme.example"
    raw = bounce_of(world.sent_to(address).data, address, day=at(START))
    world.imap.add(raw.replace(b"Status: 5.1.1", b"Status: 4.4.1"))
    assert process_inbox(world.profile, world.mailbox, world.brokers).bounces == []
    assert world.case().status is Status.SENT


def test_fixed_contact_in_registry_retries_a_bounced_case(world):
    world.cycle()
    address = "privacy@acme.example"
    world.imap.add(bounce_of(world.sent_to(address).data, address, day=at(START)))
    world.cycle()
    assert world.case().status is Status.BOUNCED
    fixed = email_broker()
    object.__setattr__(fixed, "email", "new-dpo@acme.example")
    world.brokers["acme"] = fixed
    world.cycle()
    case = world.case()
    assert case.status is Status.SENT
    assert case.contact == "new-dpo@acme.example"
    assert world.bodies_to("new-dpo@acme.example")


def test_own_bcc_copy_is_ignored(world):
    world.cycle()
    delivered = world.sent_to("privacy@acme.example")
    world.imap.add(delivered.data)
    report = process_inbox(world.profile, world.mailbox, world.brokers)
    assert report.replies == []
    assert world.case().status is Status.SENT


def test_mail_from_strangers_is_left_alone(world):
    world.cycle()
    original = world.sent_to("privacy@acme.example").data
    stripped = original.replace(b"In-Reply-To", b"X-Other")
    raw = reply_to(stripped, "news@unrelated.example", "A newsletter", day=at(START))
    message = email.message_from_bytes(raw, policy=email.policy.SMTP)
    del message["In-Reply-To"]
    message.replace_header("Subject", "Weekly deals")
    world.imap.add(message.as_bytes())
    report = process_inbox(world.profile, world.mailbox, world.brokers)
    assert report.replies == []
    assert report.unmatched == 1


def test_reply_matched_by_domain_without_threading_headers(world):
    world.cycle()
    original = world.sent_to("privacy@acme.example").data
    raw = reply_to(original, "support@mail.acme.example", "From a new thread", day=at(START))
    message = email.message_from_bytes(raw, policy=email.policy.SMTP)
    del message["In-Reply-To"]
    message.replace_header("Subject", "About your enquiry")
    world.imap.add(message.as_bytes())
    assert process_inbox(world.profile, world.mailbox, world.brokers).replies == ["acme Ltd"]


def test_reply_matched_by_reference_in_subject(world):
    world.cycle()
    ref = world.case().ref
    raw = reply_to(
        world.sent_to("privacy@acme.example").data, "agent@thirdparty.example", "x", day=at(START)
    )
    message = email.message_from_bytes(raw, policy=email.policy.SMTP)
    del message["In-Reply-To"]
    message.replace_header("Subject", f"Your case {ref}")
    world.imap.add(message.as_bytes())
    assert process_inbox(world.profile, world.mailbox, world.brokers).replies == ["acme Ltd"]


def test_daily_limit_holds_the_rest_for_tomorrow(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        brokers = registry(*(email_broker(f"b{i}") for i in range(5)))
        w = World(profile, smtp, imap, brokers)
        profile.save_settings(daily_send_limit=2)
        first = w.cycle()
        assert len(first.send.sent) == 2
        assert first.send.held_back == 3
        assert w.cycle().send.sent == []  # same day: nothing more
        go_to(START + timedelta(days=1))
        assert len(w.cycle().send.sent) == 2
        go_to(START + timedelta(days=2))
        assert len(w.cycle().send.sent) == 1
    assert {c.status for c in all_cases(profile.conn)} == {Status.SENT}


def test_complaints_count_towards_the_daily_limit(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        w = World(profile, smtp, imap, registry(*(email_broker(f"b{i}") for i in range(3))))
        w.cycle()
        profile.save_settings(daily_send_limit=2)
        go_to(date(2026, 2, 16))
        report = w.cycle()
    assert len(report.complaints) == 2
    assert report.complaints_held == 1


def test_one_bad_address_does_not_stop_the_batch(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        smtp.reject.add("privacy@a.example")
        w = World(profile, smtp, imap, registry(email_broker("a"), email_broker("b")))
        report = w.cycle()
    assert report.send.failed == ["a Ltd"]
    assert report.send.sent == ["b Ltd"]
    assert w.case("a").status is Status.QUEUED


def test_pacing_waits_between_messages(profile):
    go_to(START)
    waits = []
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        w = World(profile, smtp, imap, registry(*(email_broker(f"b{i}") for i in range(3))))
        profile.save_settings(send_delay_seconds=7)
        engine.send_requests(profile, w.brokers, w.transport, sleep=waits.append)
    assert waits == [7, 7]


def test_live_send_delivers_every_message_and_bccs_the_person(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        w = World(profile, smtp, imap, registry(*(email_broker(f"b{i}") for i in range(4))))
        report = engine.send_requests(profile, w.brokers, w.transport, sleep=NO_SLEEP)
    assert len(report.sent) == 4
    assert len(smtp.delivered) == 4
    for delivered in smtp.delivered:
        assert "test.person@example.org" in delivered.recipients


def test_evidence_is_saved_for_every_message(world):
    world.cycle()
    world.reply()
    world.cycle()
    rows = world.profile.conn.execute("SELECT * FROM message ORDER BY id").fetchall()
    assert [r["direction"] for r in rows] == ["out", "in"]
    for row in rows:
        assert (world.profile.folder / row["path"]).exists()
        assert len(row["sha256"]) == 64
    assert events.verify_chain(world.profile.conn)


def test_dry_run_sends_nothing_and_changes_nothing(world):
    world.brokers["formco"] = form_broker()
    report = engine.preview_requests(world.profile, world.brokers)
    assert len(report.written) == 1
    assert report.forms == ["formco Ltd"]
    assert world.smtp.delivered == []
    assert all_cases(world.profile.conn) == []
    assert world.profile.conn.execute("SELECT count(*) FROM event").fetchone()[0] == 0


def test_dry_run_files_are_valid_email(world):
    report = engine.preview_requests(world.profile, world.brokers)
    parsed = email.message_from_bytes(report.written[0].read_bytes(), policy=email.policy.default)
    assert parsed["To"] == "privacy@acme.example"
    assert "Article 17" in parsed.get_body().get_content()


def test_dry_cycle_previews_due_complaints_without_sending(world):
    world.cycle()
    go_to(date(2026, 2, 16))
    delivered = len(world.smtp.delivered)
    preview = cycle.preview_cycle(world.profile, world.brokers)
    assert [p.name for p in preview.complaints] == ["acme-complaint.eml"]
    assert len(world.smtp.delivered) == delivered
    assert world.case().status is Status.SENT


def test_form_broker_lifecycle(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        w = World(profile, smtp, imap, registry(form_broker()))
        assert w.cycle().send.forms == ["formco Ltd"]
        assert w.case("formco").status is Status.AWAITING_FORM
        assert smtp.delivered == []
        case = engine.form_done(profile, "formco")
        assert case.deadline_on == date(2026, 2, 15)
        go_to(date(2026, 2, 16))
        assert w.cycle().needs_you == ["formco Ltd"]  # no email address to complain to
        engine.confirm(profile, "formco", "removed")
        go_to(START + timedelta(days=60))
        assert w.cycle().resends == ["formco Ltd"]
        assert w.case("formco").status is Status.AWAITING_FORM


def test_form_done_rejected_for_other_cases(world):
    world.cycle()
    with pytest.raises(ProfileError, match="isn't waiting"):
        engine.form_done(world.profile, "acme")


def test_skip_and_unknown_outcome(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        brokers = registry(form_broker())
        w = World(profile, smtp, imap, brokers)
        w.cycle()
        assert engine.skip(profile, "formco").status is Status.SKIPPED
        with pytest.raises(ProfileError, match="Unknown outcome"):
            engine.confirm(profile, "formco", "maybe")


def test_complaint_needs_a_contact(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        w = World(profile, smtp, imap, registry(form_broker()))
        w.cycle()
        with pytest.raises(ProfileError, match="no email address"):
            engine.send_complaint(profile, w.case("formco"), w.transport)


def test_extended_tier_only_when_opted_in(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        brokers = registry(email_broker("uk"), email_broker("us", laws=("ccpa",)))
        w = World(profile, smtp, imap, brokers)
        w.cycle()
        assert [c.broker_id for c in all_cases(profile.conn)] == ["uk"]
        profile.save_settings(include_extended=True)
        w.cycle()
        assert w.case("us").tier == "extended"
        body = w.bodies_to("privacy@us.example")[0]
    assert "Article 3(2)" in body
    assert "1 Example Road" not in body


def test_no_mail_account_is_explained(store, person):
    pid, _ = store.create()
    with store.open(pid) as p:
        p.save_person(person)
        with pytest.raises(ProfileError, match="No email account"):
            engine.preview_requests(p, registry(email_broker()))
