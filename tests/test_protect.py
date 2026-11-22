"""Protect yourself: the checklist, its score, reminders, alerts, and the hub page."""

import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

import pytest

from stealth import alerts
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.models import Person
from stealth.protect import checklist
from stealth.protect import store as protect

TODAY = date(2026, 11, 23)
NOON = datetime(2026, 11, 23, 12, tzinfo=UTC)


# The checklist content ------------------------------------------------------------------------


def test_every_link_is_https_and_on_an_official_site():
    for item in checklist.ITEMS:
        assert item.links, item.id
        for link in item.links:
            parts = urlsplit(link.url)
            assert parts.scheme == "https", link.url
            assert link.host in checklist.OFFICIAL_HOSTS, link.url
            assert not parts.username and not parts.password, link.url
            assert link.label and link.source, link.url


def test_every_official_site_is_used():
    # A host left in the allowlist after its link went would let a future typo through unseen.
    used = {link.host for item in checklist.ITEMS for link in item.links}
    assert used == checklist.OFFICIAL_HOSTS


def test_the_checklist_covers_what_was_asked_for():
    assert [item.id for item in checklist.ITEMS] == [
        "open-register",
        "tps",
        "mps",
        "google-results",
        "hibp",
        "dark-web",
        "passwords",
        "phone-scams",
        "report-scams",
        "social-media",
    ]
    text = " ".join(" ".join((i.title, i.why, *i.steps)) for i in checklist.ITEMS)
    for must in ("7726", "report@phishing.gov.uk", "0345 070 0707", "2-step verification"):
        assert must in text


def test_ids_are_safe_in_urls_and_unique():
    ids = [item.id for item in checklist.ITEMS]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9-]+", i) for i in ids)


def test_the_links_were_checked_recently():
    checked = date.fromisoformat(checklist.CHECKED_ON)
    assert checked <= clock.local_date("Europe/London")


def test_recheck_periods_come_with_words():
    for item in checklist.ITEMS:
        assert bool(item.recheck_days) == bool(item.recheck_text), item.id


# Marking and the score ------------------------------------------------------------------------


def test_a_new_profile_has_everything_to_do(profile):
    assert all(entry.state is None for entry in protect.entries(profile))
    score = protect.score(profile)
    assert (score.done, score.counted, score.percent) == (0, len(checklist.ITEMS), 0)


def test_done_counts_and_not_for_me_leaves_the_score(profile):
    protect.mark(profile, "tps", protect.DONE, TODAY)
    protect.mark(profile, "social-media", protect.NOT_FOR_ME, TODAY)
    score = protect.score(profile)
    assert (score.done, score.counted) == (1, len(checklist.ITEMS) - 1)
    assert score.percent == round(100 / (len(checklist.ITEMS) - 1))


def test_marking_still_to_do_undoes_it(profile):
    protect.mark(profile, "tps", protect.DONE, TODAY)
    protect.mark(profile, "tps", None, TODAY)
    assert protect.score(profile).done == 0


def test_unknown_items_and_states_are_refused(profile):
    with pytest.raises(ProfileError):
        protect.mark(profile, "nope", protect.DONE, TODAY)
    with pytest.raises(ProfileError):
        protect.mark(profile, "tps", "maybe", TODAY)


def test_everything_not_for_me_scores_full_marks_not_a_crash(profile):
    for item in checklist.ITEMS:
        protect.mark(profile, item.id, protect.NOT_FOR_ME, TODAY)
    assert protect.score(profile).percent == 100


# Reminders ------------------------------------------------------------------------------------


def test_done_sets_a_repeating_recheck_where_one_makes_sense(profile):
    protect.mark(profile, "google-results", protect.DONE, TODAY)
    reminder = alerts.get_reminder(profile, protect.topic("google-results"))
    assert reminder.due_on == TODAY + timedelta(days=182)
    assert reminder.every_days == 182
    assert reminder.title == "Ask Google to remove results showing your contact details"
    assert reminder.link == "/protect#google-results"


def test_done_sets_no_reminder_for_one_off_steps(profile):
    protect.set_reminder(profile, "mps", 7, TODAY)
    protect.mark(profile, "mps", protect.DONE, TODAY)
    assert alerts.get_reminder(profile, protect.topic("mps")) is None


def test_not_for_me_drops_its_reminder(profile):
    protect.set_reminder(profile, "tps", 30, TODAY)
    protect.mark(profile, "tps", protect.NOT_FOR_ME, TODAY)
    assert alerts.get_reminder(profile, protect.topic("tps")) is None


def test_a_reminder_must_be_one_of_the_choices(profile):
    with pytest.raises(ProfileError):
        protect.set_reminder(profile, "tps", 3, TODAY)


def test_a_due_reminder_raises_one_alert_per_due_date(profile):
    protect.set_reminder(profile, "tps", 7, TODAY)
    assert alerts.raise_due(profile, TODAY + timedelta(days=6)) == []
    raised = alerts.raise_due(profile, TODAY + timedelta(days=7))
    assert [a.title for a in raised] == ["Register with the Telephone Preference Service"]
    assert alerts.raise_due(profile, TODAY + timedelta(days=8)) == []  # not again
    assert len(alerts.open_alerts(profile)) == 1


def test_doing_the_step_clears_its_alert_and_moves_the_recheck_on(profile):
    protect.mark(profile, "hibp", protect.DONE, TODAY)
    later = TODAY + timedelta(days=90)
    assert len(alerts.raise_due(profile, later)) == 1
    protect.mark(profile, "hibp", protect.DONE, later)
    assert alerts.open_alerts(profile) == []
    assert alerts.get_reminder(profile, protect.topic("hibp")).due_on == later + timedelta(days=90)


def test_turning_a_reminder_off_clears_its_alert(profile):
    protect.set_reminder(profile, "tps", 7, TODAY)
    alerts.raise_due(profile, TODAY + timedelta(days=7))
    protect.clear_reminder(profile, "tps")
    assert alerts.open_alerts(profile) == []
    assert alerts.get_reminder(profile, protect.topic("tps")) is None


# Alerts, for any part of Stealth --------------------------------------------------------------


def test_alerts_are_shown_once_by_the_tray_and_stay_until_dismissed(profile):
    first = alerts.add_alert(profile, "test:a", title="A", message="first")
    alerts.add_alert(profile, "test:b", title="B", message="second")
    assert [a.title for a in alerts.to_notify(profile)] == ["A", "B"]
    alerts.mark_notified(profile, [a.id for a in alerts.to_notify(profile)])
    assert alerts.to_notify(profile) == []
    assert len(alerts.open_alerts(profile)) == 2
    alerts.dismiss(profile, first.id)
    assert [a.title for a in alerts.open_alerts(profile)] == ["B"]


def test_a_generic_repeating_reminder_moves_on_when_done(profile):
    alerts.remind(profile, "credit:x", title="Check", message="m", due_on=TODAY, every_days=30)
    alerts.raise_due(profile, TODAY)
    moved = alerts.done(profile, "credit:x", TODAY)
    assert moved.due_on == TODAY + timedelta(days=30)
    assert alerts.open_alerts(profile) == []
    alerts.remind(profile, "once", title="Once", message="m", due_on=TODAY)
    assert alerts.done(profile, "once", TODAY) is None
    assert alerts.get_reminder(profile, "once") is None


def test_topic_prefixes_are_matched_literally(profile):
    alerts.add_alert(profile, "protect:x", title="P", message="m")
    alerts.add_alert(profile, "protectXx", title="Q", message="m")
    alerts.add_alert(profile, "pro_ect:x", title="R", message="m")
    assert [a.title for a in alerts.open_alerts(profile, "protect:")] == ["P"]
    assert [a.title for a in alerts.open_alerts(profile, "pro_")] == ["R"]


# The hub page ---------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from stealth_app.scheduler import Scheduler  # noqa: E402
from stealth_app.server import create_app  # noqa: E402
from stealth_app.workspace import RealWorkspace  # noqa: E402

TOKEN = "t" * 43
PORT = 50125
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


def test_the_hub_lists_every_step_with_its_links(client):
    page = client.get("/protect")
    assert page.status_code == 200
    assert "<h1>Protect yourself</h1>" in page.text
    for item in checklist.ITEMS:
        assert f'id="{item.id}"' in page.text
        for link in item.links:
            href = link.url.replace("&", "&amp;")
            assert f'href="{href}" target="_blank" rel="noopener noreferrer"' in page.text
    assert "0/10" in page.text
    assert 'href="/protect" title="Protect yourself" aria-current="page"' in page.text


def test_marking_done_from_the_page(client, workspace):
    response = post(client, "/protect/tps/mark", state="done")
    assert response.status_code == 303
    assert response.headers["location"] == "/protect#tps"
    page = client.get("/protect").text
    assert "1/10" in page
    assert re.search(r'data-item="tps".*?Done', page, re.S)
    post(client, "/protect/social-media/mark", state="not_for_me")
    assert "1/9" in client.get("/protect").text


def test_a_bad_state_or_item_is_refused_politely(client):
    assert post(client, "/protect/tps/mark", state="maybe").status_code == 400
    assert post(client, "/protect/nope/mark", state="done").status_code == 400
    assert post(client, "/protect/tps/remind", days="soon").status_code == 400


def test_a_reminder_becomes_an_alert_in_the_app_and_the_sidebar(client, workspace):
    post(client, "/protect/tps/remind", days="7")
    assert "Stealth will remind you on" in client.get("/protect").text
    clock.set_time(NOON + timedelta(days=7))
    overview = client.get("/").text
    assert "Register with the Telephone Preference Service" in overview
    assert 'aria-label="1 waiting"' in overview  # the count beside Protect yourself


def test_dismissing_an_alert_goes_back_only_to_a_stealth_page(client, workspace):
    with workspace.store.open() as profile:
        alert = alerts.add_alert(profile, "test:x", title="X", message="m")
        other = alerts.add_alert(profile, "test:y", title="Y", message="m")
    response = post(client, f"/alerts/{alert.id}/dismiss", next="/protect")
    assert response.headers["location"] == "/protect"
    for sneaky in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
        response = post(client, f"/alerts/{other.id}/dismiss", next=sneaky)
        assert response.headers["location"] == "/"
    with workspace.store.open() as profile:
        assert alerts.open_alerts(profile) == []


def test_the_tray_shows_a_due_reminder_once(workspace):
    clock.set_time(NOON)
    with workspace.store.open() as profile:
        protect.set_reminder(profile, "tps", 7, TODAY)
    notes = []
    scheduler = Scheduler(workspace, notify=lambda t, m: notes.append((t, m)), now=clock.now)
    scheduler.tick(force=True)
    assert notes == []
    clock.set_time(NOON + timedelta(days=7))
    scheduler.tick(force=True)
    scheduler.tick(force=True)
    assert notes == [
        (
            "Stealth: Sam Sample",
            "Register with the Telephone Preference Service. "
            "You asked Stealth to remind you about this.",
        )
    ]


def test_several_due_reminders_make_one_tray_message(workspace):
    clock.set_time(NOON)
    with workspace.store.open() as profile:
        protect.set_reminder(profile, "tps", 7, TODAY)
        protect.set_reminder(profile, "mps", 7, TODAY)
    notes = []
    scheduler = Scheduler(workspace, notify=lambda t, m: notes.append((t, m)), now=clock.now)
    clock.set_time(NOON + timedelta(days=7))
    scheduler.tick(force=True)
    assert notes == [("Stealth: Sam Sample", "2 reminders are due. Open Stealth to see them.")]
