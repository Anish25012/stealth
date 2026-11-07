"""Running by itself: when a daily check is due, what it does, and what happens when it can't."""

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from stealth.cases import engine
from stealth.cases.state import Status, find_case
from stealth.core import clock
from stealth.devmail.servers import LocalImap, LocalSmtp, reply_to
from stealth.profiles.models import DEFAULT_SETTINGS, MailAccount, Person, Settings
from stealth_app import autostart
from stealth_app.scheduler import STARTUP_GRACE, Scheduler, is_due
from stealth_app.workspace import RealWorkspace
from tests.helpers import email_broker, registry

MONDAY_10AM = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
PASSWORD = "pw"


def settings(**changes):
    return Settings(**{**DEFAULT_SETTINGS, **changes})


def at(hour, day=MONDAY_10AM):
    return day.replace(hour=hour)


# When it's due ---------------------------------------------------------------------------------


def test_a_new_profile_is_checked_straight_away():
    assert is_due(settings(), MONDAY_10AM)


def test_only_once_a_day():
    assert not is_due(settings(last_auto_run=at(9).isoformat()), at(18))


def test_waits_for_the_chosen_hour_after_yesterdays_check():
    yesterday = (MONDAY_10AM - timedelta(days=1)).replace(hour=9)
    assert not is_due(settings(last_auto_run=yesterday.isoformat(), auto_run_hour=11), at(10))
    assert is_due(settings(last_auto_run=yesterday.isoformat(), auto_run_hour=11), at(11))


def test_catches_up_at_once_after_a_missed_day():
    three_days_ago = MONDAY_10AM - timedelta(days=3)
    early = at(7)
    assert is_due(settings(last_auto_run=three_days_ago.isoformat(), auto_run_hour=9), early)


def test_never_when_turned_off():
    assert not is_due(settings(auto_run=False), MONDAY_10AM)


# What a check does -----------------------------------------------------------------------------


class Workspace(RealWorkspace):
    """The real workspace, with test companies and no pacing delay."""

    def __init__(self, store, brokers):
        super().__init__(store)
        self._brokers = brokers

    def sleep(self, seconds):
        pass


@pytest.fixture
def servers():
    with LocalSmtp(password=PASSWORD) as smtp, LocalImap(PASSWORD) as imap:
        yield smtp, imap


def make_profile(store, smtp_port, imap_port, name="Sam Sample"):
    profile_id, _code = store.create()
    with store.open(profile_id) as profile:
        profile.save_person(Person(display_name=name, names=[name], emails=["sam@example.org"]))
        profile.save_mail_account(
            MailAccount(
                provider="smtp",
                address="sam@example.org",
                smtp_host="127.0.0.1",
                smtp_port=smtp_port,
                smtp_security="none",
                imap_host="127.0.0.1",
                imap_port=imap_port,
                imap_security="none",
                username="sam@example.org",
            ),
            password=PASSWORD,
        )
    return profile_id


@pytest.fixture
def setup(store, servers):
    smtp, imap = servers
    brokers = registry(email_broker("acme"), email_broker("other"))
    workspace = Workspace(store, brokers)
    profile_id = make_profile(store, smtp.port, imap.port)
    clock.set_time(MONDAY_10AM)
    with store.open(profile_id) as profile:
        engine.open_cases(profile, brokers, hold=True)
        engine.approve(profile, ["acme"])  # approved; "other" is still waiting for an OK
    notes = []
    scheduler = Scheduler(workspace, notify=lambda t, m: notes.append((t, m)), now=clock.now)
    scheduler.started -= STARTUP_GRACE
    return scheduler, workspace, profile_id, smtp, imap, notes


def test_a_check_sends_only_approved_letters_and_records_what_happened(setup):
    scheduler, workspace, profile_id, smtp, _imap, _notes = setup
    [outcome] = scheduler.tick()
    assert outcome.ran
    assert [d.recipients[0] for d in smtp.delivered] == ["privacy@acme.example"]
    with workspace.store.open(profile_id) as profile:
        assert find_case(profile.conn, "other").status is Status.DRAFT  # never sent unapproved
        saved = profile.settings()
    assert saved.last_auto_run == MONDAY_10AM.isoformat()
    assert "1 approved letter(s) sent" in saved.last_auto_result
    assert "1 thing needs you" in saved.last_auto_result  # the letter still waiting for an OK

    assert scheduler.tick()[0].ran is False  # not again the same day
    assert len(smtp.delivered) == 1


def test_a_reply_the_next_day_is_found_and_the_person_is_told(setup):
    scheduler, _workspace, _profile_id, smtp, imap, notes = setup
    scheduler.tick()
    letter = smtp.delivered[0].message
    clock.set_time(MONDAY_10AM + timedelta(days=1))
    imap.add(reply_to(letter.as_bytes(), "privacy@acme.example", "Done.", day=clock.now()))
    [outcome] = scheduler.tick()
    assert outcome.ran and outcome.notify
    assert notes == [("Stealth: Sam Sample", outcome.result)]
    assert "1 new reply to read" in outcome.result


def test_no_internet_doesnt_use_up_the_day_and_tries_again_later(store):
    brokers = registry(email_broker("acme"))
    workspace = Workspace(store, brokers)
    profile_id = make_profile(store, 1, 1)  # nothing listens on port 1
    clock.set_time(MONDAY_10AM)
    now = [MONDAY_10AM]
    scheduler = Scheduler(workspace, now=lambda: now[0])
    scheduler.started -= STARTUP_GRACE
    [outcome] = scheduler.tick()
    assert not outcome.ran
    with store.open(profile_id) as profile:
        assert profile.settings().last_auto_run is None
    assert scheduler.tick() == []  # waiting before the retry
    now[0] += timedelta(minutes=31)
    assert len(scheduler.tick()) == 1  # tries again


def test_a_wrong_password_is_reported_once_not_every_few_minutes(store, servers):
    smtp, imap = servers
    workspace = Workspace(store, registry(email_broker("acme")))
    profile_id = make_profile(store, smtp.port, imap.port)
    with store.open(profile_id) as profile:
        profile.save_mail_account(profile.mail_account(), password="wrong")
    clock.set_time(MONDAY_10AM)
    notes = []
    scheduler = Scheduler(workspace, notify=lambda t, m: notes.append(m), now=clock.now)
    scheduler.started -= STARTUP_GRACE
    scheduler.tick()
    scheduler.tick()
    assert len(notes) == 1 and "Couldn't sign in to your email" in notes[0]


def test_it_waits_while_something_else_holds_the_lock(setup):
    scheduler, workspace, _profile_id, smtp, _imap, _notes = setup
    with workspace.lock:
        [outcome] = scheduler.tick()
    assert not outcome.ran and smtp.delivered == []


def test_nothing_runs_in_the_first_minutes_after_starting(setup):
    scheduler, *_ = setup
    scheduler.started = MONDAY_10AM
    assert scheduler.tick() == []
    assert scheduler.tick(force=True)  # "Check now" from the tray doesn't wait


def test_every_person_is_checked(store, servers):
    smtp, imap = servers
    workspace = Workspace(store, registry(email_broker("acme")))
    for name in ("First", "Second"):
        make_profile(store, smtp.port, imap.port, name=name)
    clock.set_time(MONDAY_10AM)
    scheduler = Scheduler(workspace, now=clock.now)
    scheduler.started -= STARTUP_GRACE
    assert sorted(o.name for o in scheduler.tick() if o.ran) == ["First", "Second"]


# Start with Windows ----------------------------------------------------------------------------


def test_start_with_windows_is_only_offered_by_the_installed_app():
    assert not autostart.available()  # tests run from source
    autostart.set_enabled(True)  # a no-op from source: nothing is written
    assert not autostart.is_enabled()


@pytest.mark.skipif("sys.platform != 'win32'")
def test_start_with_windows_writes_and_removes_the_run_entry(monkeypatch):
    import winreg

    monkeypatch.setattr(autostart, "RUN_KEY", r"Software\StealthTests\Run")
    monkeypatch.setattr(autostart, "available", lambda: True)
    try:
        autostart.set_enabled(True)
        assert autostart.is_enabled()
        assert autostart.command().endswith(" --background")
        autostart.set_enabled(False)
        assert not autostart.is_enabled()
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\StealthTests\Run")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\StealthTests")
