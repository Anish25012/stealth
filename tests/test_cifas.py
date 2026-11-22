"""Cifas Protective Registration: when it started, when it ends, and the reminders between."""

import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

import pytest

from stealth import alerts
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.models import Person
from stealth.protect import cifas

TODAY = date(2026, 11, 23)
NOON = datetime(2026, 11, 23, 12, tzinfo=UTC)


def test_links_are_cifas_own_https_pages():
    for link in (cifas.ABOUT, cifas.APPLY):
        assert urlsplit(link.url).scheme == "https"
        assert link.host in cifas.OFFICIAL_HOSTS


@pytest.mark.parametrize(
    ("start", "months", "end"),
    [
        (date(2026, 11, 23), 24, date(2028, 11, 23)),
        (date(2028, 2, 29), 24, date(2030, 2, 28)),  # no 29 February then
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2026, 12, 15), 1, date(2027, 1, 15)),
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
    ],
)
def test_add_months(start, months, end):
    assert cifas.add_months(start, months) == end


def test_nothing_recorded_on_a_new_profile(profile):
    registration = cifas.status(profile, TODAY)
    assert (registration.recorded, registration.state) == (False, cifas.NONE)


def test_recording_sets_the_end_and_two_reminders(profile):
    registration = cifas.record(profile, TODAY, TODAY)
    assert registration.ends_on == date(2028, 11, 23)
    assert registration.state == cifas.ACTIVE
    due = {r.topic: r.due_on for r in alerts.reminders(profile, cifas.TOPIC)}
    assert due == {"cifas:ending": date(2028, 10, 24), "cifas:ended": date(2028, 11, 23)}
    ending = alerts.get_reminder(profile, "cifas:ending")
    assert "23 November 2028" in ending.message
    assert "won't email you" in ending.message
    assert ending.every_days is None  # one-offs: a renewal is a new registration


def test_the_states_as_time_passes(profile):
    cifas.record(profile, TODAY, TODAY)
    assert cifas.status(profile, date(2028, 10, 23)).state == cifas.ACTIVE
    assert cifas.status(profile, date(2028, 10, 24)).state == cifas.ENDING_SOON
    assert cifas.status(profile, date(2028, 11, 23)).state == cifas.ENDED


def test_each_reminder_alerts_once_on_its_day(profile):
    cifas.record(profile, TODAY, TODAY)
    assert alerts.raise_due(profile, date(2028, 10, 23)) == []
    soon = alerts.raise_due(profile, date(2028, 10, 24))
    assert [a.title for a in soon] == ["Your Cifas Protective Registration ends soon"]
    ended = alerts.raise_due(profile, date(2028, 11, 23))
    assert [a.title for a in ended] == ["Your Cifas Protective Registration has ended"]
    assert alerts.raise_due(profile, date(2028, 12, 1)) == []


def test_renewing_replaces_the_dates_and_clears_old_alerts(profile):
    cifas.record(profile, TODAY, TODAY)
    renewed_on = date(2028, 11, 1)
    alerts.raise_due(profile, renewed_on)  # the "ends soon" alert is showing
    registration = cifas.record(profile, renewed_on, renewed_on)
    assert registration.ends_on == date(2030, 11, 1)
    assert alerts.open_alerts(profile, cifas.TOPIC) == []
    due = {r.topic: r.due_on for r in alerts.reminders(profile, cifas.TOPIC)}
    assert due == {"cifas:ending": date(2030, 10, 2), "cifas:ended": date(2030, 11, 1)}


def test_a_registration_already_near_its_end_gets_only_the_end_reminder(profile):
    started = cifas.add_months(TODAY, -24) + timedelta(days=10)  # ends in 10 days
    registration = cifas.record(profile, started, TODAY)
    assert registration.state == cifas.ENDING_SOON
    assert [r.topic for r in alerts.reminders(profile, cifas.TOPIC)] == ["cifas:ended"]


def test_an_already_ended_registration_sets_no_reminders(profile):
    registration = cifas.record(profile, date(2024, 1, 1), TODAY)
    assert registration.state == cifas.ENDED
    assert alerts.reminders(profile, cifas.TOPIC) == []


def test_future_and_ancient_start_dates_are_refused(profile):
    with pytest.raises(ProfileError):
        cifas.record(profile, TODAY + timedelta(days=1), TODAY)
    with pytest.raises(ProfileError):
        cifas.record(profile, date(2020, 1, 1), TODAY)


def test_forgetting_clears_everything(profile):
    cifas.record(profile, TODAY, TODAY)
    alerts.raise_due(profile, date(2028, 10, 24))
    cifas.forget(profile)
    assert cifas.status(profile, TODAY).state == cifas.NONE
    assert alerts.reminders(profile, cifas.TOPIC) == []
    assert alerts.open_alerts(profile, cifas.TOPIC) == []


def test_other_reminders_are_left_alone(profile):
    alerts.remind(profile, "credit:experian", title="C", message="m", due_on=TODAY)
    cifas.record(profile, TODAY, TODAY)
    cifas.forget(profile)
    assert alerts.get_reminder(profile, "credit:experian") is not None


# The page -------------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from stealth_app.server import create_app  # noqa: E402
from stealth_app.workspace import RealWorkspace  # noqa: E402

TOKEN = "t" * 43
PORT = 50127
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def client(store):
    profile_id, _code = store.create()
    with store.open(profile_id) as opened:
        opened.save_person(Person(display_name="Sam Sample", names=["Sam Sample"]))
    clock.set_time(NOON)
    client = TestClient(create_app(TOKEN, PORT, workspace=RealWorkspace(store)), base_url=BASE)
    client.follow_redirects = False
    assert client.get("/auth", params={"t": TOKEN}).status_code == 303
    return client


def post(client, url, **fields):
    return client.post(url, data={"csrf": client.app.state.dashboard.csrf, **fields})


def card(client):
    match = re.search(
        r'<section class="card" id="cifas".*?</section>', client.get("/protect").text, re.S
    )
    assert match
    return match.group(0)


def test_the_card_explains_it_and_links_to_cifas(client):
    html = card(client)
    assert 'data-cifas="none"' in html
    assert "£30 for" in html and "two years" in html
    for link in (cifas.ABOUT, cifas.APPLY):
        assert f'href="{link.url}" target="_blank" rel="noopener noreferrer"' in html
    assert 'type="date" value="2026-11-23" max="2026-11-23"' in html
    assert "Stop tracking it" not in html


def test_recording_and_stopping_from_the_page(client):
    response = post(client, "/protect/cifas/record", started="2026-11-01")
    assert response.status_code == 303
    assert response.headers["location"] == "/protect#cifas"
    html = card(client)
    assert 'data-cifas="active"' in html
    assert "started on 1 November 2026 and ends on 1 November 2028" in html
    assert "In place" in html and "Apply again" in html
    post(client, "/protect/cifas/forget")
    assert 'data-cifas="none"' in card(client)


def test_bad_dates_are_refused_politely(client):
    assert post(client, "/protect/cifas/record", started="").status_code == 400
    assert post(client, "/protect/cifas/record", started="1 Nov").status_code == 400
    assert post(client, "/protect/cifas/record", started="2027-01-01").status_code == 400


def test_the_ending_reminder_reaches_the_overview(client):
    post(client, "/protect/cifas/record", started="2026-11-23")
    clock.set_time(datetime(2028, 10, 24, 12, tzinfo=UTC))
    assert "Your Cifas Protective Registration ends soon" in client.get("/").text
    assert "Ends soon" in card(client)
