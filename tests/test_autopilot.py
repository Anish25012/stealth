"""Autopilot: the daily check sends, repeats and complains by itself, and records all of it."""

from datetime import date

import pytest

from stealth import autopilot, cycle
from stealth.cases import engine
from stealth.cases.state import Status, all_cases, find_case
from stealth.db import events
from stealth.devmail.servers import LocalImap, LocalSmtp
from stealth.mail.smtp_imap import ImapMailbox, junk_folder
from stealth_app import views
from tests.helpers import email_broker, registry
from tests.test_lifecycle import START, World, go_to

BROKERS = registry(
    email_broker("acme"),
    email_broker("bravo"),
    email_broker("charlie"),
    email_broker("optional", tier="extended"),
)


@pytest.fixture
def world(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        w = World(profile, smtp, imap, BROKERS)
        # The World follows the command line's automatic path; these tests set things themselves.
        profile.save_settings(
            auto_complaints=False, daily_send_limit=2, include_extended=True, autopilot=False
        )
        yield w


def app_cycle(world):
    """The desktop app's daily check: drafts wait unless Autopilot sends them."""
    return cycle.run_cycle(
        world.profile,
        world.brokers,
        world.transport,
        world.mailbox,
        sleep=lambda _s: None,
        hold_for_approval=True,
    )


def statuses(world):
    return {c.broker_id: c.status for c in all_cases(world.profile.conn)}


def test_new_profiles_start_on_and_old_ones_stay_off(store, person):
    from stealth.profiles.models import MailAccount
    from stealth.setup import new_profile

    profile_id, _code = new_profile(
        store, person, MailAccount("smtp", "test.person@example.org"), "pw", check_mail=False
    )
    with store.open(profile_id) as profile:
        settings = profile.settings()
    assert settings.autopilot is True and settings.autopilot_explained is False
    from stealth.profiles.models import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["autopilot"] is False  # a profile from before Autopilot keeps it off


def test_with_autopilot_off_the_app_sends_nothing_unapproved(world):
    report = app_cycle(world)
    assert report.send.sent == [] and report.autopilot_sent == []
    assert set(statuses(world).values()) == {Status.DRAFT}
    assert world.smtp.delivered == []


def test_autopilot_sends_core_letters_in_daily_batches(world):
    world.profile.save_settings(autopilot=True)
    report = app_cycle(world)
    assert len(report.autopilot_sent) == 2  # the daily limit
    first = statuses(world)
    assert sorted(first.values(), key=str).count(Status.SENT) == 2
    assert first["optional"] is Status.DRAFT  # optional companies still ask
    waiting = [b for b, s in first.items() if s is Status.DRAFT and b != "optional"]
    assert len(waiting) == 1  # the rest wait as drafts, not already queued
    go_to(date(2026, 1, 16))
    assert len(app_cycle(world).autopilot_sent) == 1
    assert statuses(world)["optional"] is Status.DRAFT
    assert {s for b, s in statuses(world).items() if b != "optional"} == {Status.SENT}


def test_every_automatic_letter_is_in_the_audit_trail_and_the_list(world):
    world.profile.save_settings(autopilot=True)
    app_cycle(world)
    done = autopilot.sent_automatically(world.profile)
    assert len(done) == 2 and {d.what for d in done} == {"request"}
    for item in done:
        trail = events.for_case(world.profile.conn, item.case.id)
        approved = [e for e in trail if e["payload"].get("reason") == "approved by Autopilot"]
        assert approved and approved[0]["actor"] == autopilot.ACTOR
        assert item.detail == f"sent to {item.case.contact}"
        history = views.timeline(world.profile, item.case)
        assert any(e.text == "Sent automatically: first letter" for e in history)
    assert events.verify_chain(world.profile.conn)


def test_turning_it_off_leaves_the_rest_unsent(world):
    world.profile.save_settings(autopilot=True)
    app_cycle(world)
    world.profile.save_settings(autopilot=False)
    go_to(date(2026, 1, 16))
    before = len(world.smtp.delivered)
    assert app_cycle(world).send.sent == []
    assert len(world.smtp.delivered) == before


def test_a_missed_deadline_gets_its_complaint_automatically(world):
    world.profile.save_settings(autopilot=True, daily_send_limit=40, include_extended=False)
    app_cycle(world)
    go_to(date(2026, 2, 16))  # the day after every deadline
    report = app_cycle(world)
    assert sorted(report.complaints) == ["acme Ltd", "bravo Ltd", "charlie Ltd"]
    assert report.complaints_waiting == []
    complaints = [d for d in autopilot.sent_automatically(world.profile) if d.what == "complaint"]
    assert len(complaints) == 3 and {d.detail for d in complaints} == {"no reply by the deadline"}
    assert find_case(world.profile.conn, "acme").status is Status.COMPLAINT_SENT


def test_a_reply_that_arrived_first_stops_the_complaint(world):
    world.profile.save_settings(autopilot=True, daily_send_limit=40, include_extended=False)
    app_cycle(world)
    world.reply("acme", "We have deleted all the personal data we held about you.")
    go_to(date(2026, 2, 16))
    report = app_cycle(world)
    assert "acme Ltd" not in report.complaints
    assert find_case(world.profile.conn, "acme").status is not Status.COMPLAINT_SENT


def test_a_refusal_gets_its_complaint_automatically(world):
    world.profile.save_settings(autopilot=True, daily_send_limit=40, include_extended=False)
    app_cycle(world)
    engine.confirm(world.profile, "acme", "refused")
    row = next(r for r in views.rows(world.profile) if r.case.broker_id == "acme")
    assert row.info.group == "progress" and row.info.label == "Complaint going out"
    go_to(date(2026, 1, 16))
    report = app_cycle(world)
    assert report.complaints == ["acme Ltd"]
    body = world.bodies_to("privacy@acme.example")[-1]
    assert "refused my request" in " ".join(body.split())
    done = [d for d in autopilot.sent_automatically(world.profile) if d.what == "complaint"]
    assert done[0].detail == "they refused"


def test_repeat_requests_go_automatically(world):
    world.profile.save_settings(autopilot=True, daily_send_limit=40, include_extended=False)
    app_cycle(world)
    engine.confirm(world.profile, "acme", "removed")
    due = find_case(world.profile.conn, "acme").next_action_on
    go_to(due)
    report = app_cycle(world)
    assert "acme Ltd" in report.autopilot_sent
    case = find_case(world.profile.conn, "acme")
    assert case.status is Status.SENT and case.round == 2
    assert autopilot.sent_automatically(world.profile)[0].what == "repeat"


def test_needs_you_shows_only_what_needs_a_person(world):
    world.profile.save_settings(autopilot=True, daily_send_limit=1)
    app_cycle(world)
    groups = views.grouped(views.rows(world.profile))
    # The core letters waiting for tomorrow's batch don't ask; the optional company does.
    assert [r.case.broker_id for r in groups["approve"]] == ["optional"]
    autopilot_rows = [r for r in groups["progress"] if r.info.label == "Autopilot sends soon"]
    assert len(autopilot_rows) == 2  # three core companies, a limit of one a day
    assert "next daily check" in autopilot_rows[0].explanation


# The spam folder ------------------------------------------------------------------------------


def test_the_junk_folder_is_found_by_its_flag():
    listing = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"',
    ]
    assert junk_folder(listing) == "[Gmail]/Spam"
    assert junk_folder([b'(\\HasNoChildren \\Junk) "." Junk']) == "Junk"
    assert junk_folder([b'(\\HasNoChildren) "/" INBOX']) is None
    assert junk_folder([]) is None


class FakeServer:
    """Just enough IMAP: an inbox and a spam folder, each with one message."""

    def __init__(self, list_works=True):
        self.selected = None
        self.list_works = list_works

    def list(self):
        if not self.list_works:
            raise OSError("LIST not supported")
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Junk) "/" "[Gmail]/Spam"']

    def select(self, name, readonly=False):
        self.selected = name
        return "OK", [b"1"]

    def search(self, _charset, *_criteria):
        return "OK", [b"1"]

    def fetch(self, _number, _parts):
        return "OK", [(b"1", f"Subject: in {self.selected}\r\n\r\nhi".encode())]


def test_replies_in_spam_are_read_too():
    server = FakeServer()
    found = ImapMailbox._fetch(server, START) + ImapMailbox._fetch_junk(server, START)
    assert len(found) == 2 and server.selected == '"[Gmail]/Spam"'
    assert b'in "[Gmail]/Spam"' in found[1].raw


def test_a_server_without_a_spam_folder_is_fine():
    assert ImapMailbox._fetch_junk(FakeServer(list_works=False), START) == []


# The app --------------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from tests.test_dashboard import app, client, demo, post, real, wait_for_job  # noqa: E402, F401
from tests.test_dashboard import statuses as demo_statuses  # noqa: E402


def test_the_one_time_explanation_offers_autopilot_to_an_existing_profile(client, demo):  # noqa: F811
    page = client.get("/").text
    assert "data-autopilot-intro" in page and "Let Stealth run itself?" in page
    assert "It still asks you before anything to do with" in page
    assert post(client, "/autopilot/choose", autopilot="no").status_code == 303
    with demo.store.open() as profile:
        settings = profile.settings()
    assert settings.autopilot is False and settings.autopilot_explained is True
    assert "data-autopilot-intro" not in client.get("/").text  # only once


def test_turning_it_on_from_the_explanation(client, demo):  # noqa: F811
    assert post(client, "/autopilot/choose", autopilot="yes").status_code == 303
    with demo.store.open() as profile:
        assert profile.settings().autopilot is True
    page = client.get("/settings").text
    assert 'id="f-autopilot" name="autopilot" value="yes" checked' in page
    assert "Autopilot is on, so complaints already go" in page


def test_on_autopilot_core_letters_dont_wait_for_an_ok_and_the_check_sends_them(client, demo):  # noqa: F811
    assert "new-lists" in client.get("/approve").text
    post(client, "/settings/autopilot", autopilot="yes")
    approve = client.get("/approve").text
    assert "new-lists" not in approve and "people-finder" not in approve
    assert "Autopilot sends soon" in client.get("/cases").text
    before = len(demo.smtp.delivered)
    job, page = wait_for_job(client, post(client, "/cycle"))
    assert job.state == "done", job.error
    assert "Sent by Autopilot:" in page.text
    # No OK needed now: the late company and the one that refused both get their complaint.
    assert "Formal complaints sent to: Refusing Corp, Silent Lists Ltd." in page.text
    recipients = {r for d in demo.smtp.delivered[before:] for r in d.recipients}
    assert {"privacy@new-lists.test", "privacy@silent-lists.test"} <= recipients
    assert demo_statuses(demo)["new-lists"] is Status.SENT
    listing = client.get("/sent-automatically").text
    assert 'data-autopilot="new-lists"' in listing and 'data-autopilot="silent-lists"' in listing
    assert "First letter" in listing and "Formal complaint" in listing
    history = client.get("/cases/silent-lists").text
    assert "Sent automatically: formal complaint" in history


def test_the_list_is_empty_before_anything_is_sent(client, demo):  # noqa: F811
    page = client.get("/sent-automatically").text
    assert "Nothing sent automatically yet." in page and "Autopilot is\n    <strong>off" in page
