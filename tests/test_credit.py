"""Credit report reminders: when each agency's report was checked, reminders and notes."""

import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

import pytest

from stealth import alerts
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.models import Person
from stealth.protect import credit

TODAY = date(2026, 11, 23)
NOON = datetime(2026, 11, 23, 12, tzinfo=UTC)


def test_the_three_uk_agencies_with_official_links():
    assert [a.name for a in credit.AGENCIES] == ["Experian", "Equifax", "TransUnion"]
    links = [a.report for a in credit.AGENCIES] + [credit.ICO_ADVICE]
    for link in links:
        assert urlsplit(link.url).scheme == "https", link.url
        assert link.host in credit.OFFICIAL_HOSTS, link.url
    assert {link.host for link in links} == credit.OFFICIAL_HOSTS


def test_nothing_checked_on_a_new_profile(profile):
    for status in credit.statuses(profile):
        assert (status.last_checked_on, status.reminder, status.note) == (None, None, "")


def test_checking_records_the_day_and_sets_a_repeating_reminder(profile):
    status = credit.mark_checked(profile, "experian", TODAY, 91)
    assert status.last_checked_on == TODAY
    assert status.reminder.due_on == TODAY + timedelta(days=91)
    assert status.reminder.every_days == 91
    assert status.reminder.title == "Check your Experian credit report"
    assert status.reminder.link == "/protect#credit-experian"
    others = [s for s in credit.statuses(profile) if s.agency.id != "experian"]
    assert all(s.last_checked_on is None and s.reminder is None for s in others)


def test_a_due_reminder_alerts_and_checking_again_clears_it(profile):
    credit.mark_checked(profile, "equifax", TODAY, 182)
    later = TODAY + timedelta(days=182)
    assert [a.title for a in alerts.raise_due(profile, later)] == [
        "Check your Equifax credit report"
    ]
    credit.mark_checked(profile, "equifax", later, 182)
    assert alerts.open_alerts(profile) == []
    status = next(s for s in credit.statuses(profile) if s.agency.id == "equifax")
    assert status.reminder.due_on == later + timedelta(days=182)


def test_changing_how_often_counts_from_the_last_check(profile):
    credit.mark_checked(profile, "transunion", TODAY, 365)
    status = credit.set_every(profile, "transunion", 91, TODAY + timedelta(days=10))
    assert status.reminder.due_on == TODAY + timedelta(days=91)


def test_changing_how_often_before_any_check_counts_from_today(profile):
    status = credit.set_every(profile, "experian", 91, TODAY)
    assert status.reminder.due_on == TODAY + timedelta(days=91)
    assert status.last_checked_on is None


def test_a_shorter_cycle_long_overdue_is_due_today_not_in_the_past(profile):
    credit.mark_checked(profile, "experian", TODAY, 365)
    later = TODAY + timedelta(days=200)
    status = credit.set_every(profile, "experian", 91, later)
    assert status.reminder.due_on == later


def test_stopping_reminders_keeps_the_last_check(profile):
    credit.mark_checked(profile, "experian", TODAY, 91)
    alerts.raise_due(profile, TODAY + timedelta(days=91))
    credit.stop_reminders(profile, "experian")
    status = next(s for s in credit.statuses(profile) if s.agency.id == "experian")
    assert (status.last_checked_on, status.reminder) == (TODAY, None)
    assert alerts.open_alerts(profile) == []


def test_notes_are_kept_and_can_be_cleared(profile):
    credit.save_note(profile, "equifax", "  A card I never opened, from 2025  ")
    credit.mark_checked(profile, "equifax", TODAY, 182)  # checking keeps the note
    status = next(s for s in credit.statuses(profile) if s.agency.id == "equifax")
    assert status.note == "A card I never opened, from 2025"
    credit.save_note(profile, "equifax", "")
    assert next(s for s in credit.statuses(profile) if s.agency.id == "equifax").note == ""


def test_unknown_agencies_odd_cycles_and_long_notes_are_refused(profile):
    with pytest.raises(ProfileError):
        credit.mark_checked(profile, "nope", TODAY, 91)
    with pytest.raises(ProfileError):
        credit.mark_checked(profile, "experian", TODAY, 1)
    with pytest.raises(ProfileError):
        credit.save_note(profile, "experian", "x" * (credit.NOTE_LIMIT + 1))


# The page -------------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from stealth_app.server import create_app  # noqa: E402
from stealth_app.workspace import RealWorkspace  # noqa: E402

TOKEN = "t" * 43
PORT = 50126
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def workspace(store):
    profile_id, _code = store.create()
    with store.open(profile_id) as opened:
        opened.save_person(Person(display_name="Sam Sample", names=["Sam Sample"]))
    return RealWorkspace(store)


@pytest.fixture
def client(workspace):
    clock.set_time(NOON)
    client = TestClient(create_app(TOKEN, PORT, workspace=workspace), base_url=BASE)
    client.follow_redirects = False
    assert client.get("/auth", params={"t": TOKEN}).status_code == 303
    return client


def post(client, url, **fields):
    return client.post(url, data={"csrf": client.app.state.dashboard.csrf, **fields})


def agency_block(page, agency_id):
    match = re.search(rf'<li class="credit-agency" id="credit-{agency_id}".*?</li>', page, re.S)
    assert match, agency_id
    return match.group(0)


def test_the_page_shows_each_agency_and_its_free_report(client):
    page = client.get("/protect").text
    assert "Your credit reports" in page
    for agency in credit.AGENCIES:
        block = agency_block(page, agency.id)
        assert "Not checked yet." in block
        assert f'href="{agency.report.url}" target="_blank" rel="noopener noreferrer"' in block
        assert 'value="182" selected' in block  # six months unless chosen otherwise


def test_checking_from_the_page(client):
    response = post(client, "/protect/credit/transunion/checked", every="91")
    assert response.status_code == 303
    assert response.headers["location"] == "/protect#credit-transunion"
    block = agency_block(client.get("/protect").text, "transunion")
    assert "Last checked 23 November 2026." in block
    assert "Next reminder 22 February 2027." in block
    assert 'value="91" selected' in block
    assert "Stop reminders" in block


def test_stopping_and_notes_from_the_page(client, workspace):
    post(client, "/protect/credit/experian/checked", every="182")
    post(client, "/protect/credit/experian/remind", every="off")
    post(client, "/protect/credit/experian/note", note="<script>x</script> unknown search")
    block = agency_block(client.get("/protect").text, "experian")
    assert "Next reminder" not in block
    assert "&lt;script&gt;x&lt;/script&gt; unknown search" in block  # escaped, never run
    assert "<script>x" not in block


def test_bad_input_from_the_page_is_refused_politely(client):
    assert post(client, "/protect/credit/nope/checked", every="91").status_code == 400
    assert post(client, "/protect/credit/experian/checked", every="soon").status_code == 400
    assert post(client, "/protect/credit/experian/remind", every="5").status_code == 400


def test_a_due_credit_reminder_shows_as_an_alert(client):
    post(client, "/protect/credit/equifax/checked", every="91")
    clock.set_time(NOON + timedelta(days=91))
    assert "Check your Equifax credit report" in client.get("/").text
