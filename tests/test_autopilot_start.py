"""Autopilot's start date: drafts wait until the day the person chose, then go in daily batches."""

from datetime import date

import pytest

from stealth.cases.state import Status
from stealth_app import views
from tests.test_autopilot import BROKERS, app_cycle, statuses
from tests.test_lifecycle import World, go_to

pytest.importorskip("fastapi")

from stealth.devmail.servers import LocalImap, LocalSmtp

DECEMBER = date(2026, 12, 1)


@pytest.fixture
def world(profile):
    go_to(date(2026, 11, 25))
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        w = World(profile, smtp, imap, BROKERS)
        profile.save_settings(
            autopilot=True,
            autopilot_from=DECEMBER.isoformat(),
            auto_complaints=False,
            daily_send_limit=2,
            include_extended=False,
        )
        yield w


def core(world):
    return {b: s for b, s in statuses(world).items() if b != "optional"}


def test_nothing_goes_before_the_start_date(world):
    for day in (25, 28, 30):
        go_to(date(2026, 11, day))
        assert app_cycle(world).autopilot_sent == []
    assert set(core(world).values()) == {Status.DRAFT}
    assert world.smtp.delivered == []


def test_drafts_say_when_they_will_go(world):
    app_cycle(world)
    row = next(r for r in views.rows(world.profile) if r.case.broker_id == "acme")
    assert row.info.label == "Autopilot sends soon"
    assert (
        row.explanation
        == "Autopilot sends this letter from 1 December 2026, within your daily limit."
    )


def test_from_the_start_date_they_go_in_daily_batches(world):
    app_cycle(world)
    go_to(DECEMBER)
    assert len(app_cycle(world).autopilot_sent) == 2
    go_to(date(2026, 12, 2))
    assert len(app_cycle(world).autopilot_sent) == 1
    assert set(core(world).values()) == {Status.SENT}


# Settings and the command line ------------------------------------------------------------

from tests.test_dashboard import app, client, demo, post, real  # noqa: E402, F401


def test_the_start_date_is_set_in_settings(client, demo):  # noqa: F811
    assert 'id="f-autopilot_from"' in client.get("/settings").text
    post(client, "/settings/autopilot", autopilot="yes", autopilot_from="2026-12-01")
    with demo.store.open() as profile:
        settings = profile.settings()
    assert settings.autopilot and settings.autopilot_from == "2026-12-01"
    assert 'value="2026-12-01"' in client.get("/settings").text
    post(client, "/settings/autopilot", autopilot="yes", autopilot_from="")
    with demo.store.open() as profile:
        assert profile.settings().autopilot_from is None


def test_a_bad_start_date_is_refused(client, demo):  # noqa: F811
    response = post(client, "/settings/autopilot", autopilot="yes", autopilot_from="next week")
    assert response.status_code in (400, 422) or "isn't a date" in response.text
    with demo.store.open() as profile:
        assert profile.settings().autopilot_from is None
