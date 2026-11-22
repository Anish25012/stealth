"""Breach monitoring: the HIBP provider (with a fake opener only), recording, alerts and score."""

import json
import re
from datetime import UTC, date, datetime, timedelta

import pytest

from stealth import alerts
from stealth.breaches import hibp
from stealth.breaches import store as breaches
from stealth.breaches.provider import (
    Breach,
    BreachCheckError,
    BreachKeyError,
    FakeProvider,
    ManualProvider,
    html_to_text,
    manual_breach,
)
from stealth.core import clock
from stealth.core.errors import ProfileError, StealthError

NOON = datetime(2026, 11, 23, 12, tzinfo=UTC)
KEY = "0123456789abcdef0123456789abcdef"
ADOBE = {
    "Name": "Adobe",
    "Title": "Adobe",
    "Domain": "adobe.com",
    "BreachDate": "2013-10-04",
    "DataClasses": ["Email addresses", "Password hints", "Passwords", "Usernames"],
    "Description": 'In October 2013, <a href="https://example.org">153 million</a> Adobe '
    "accounts were breached &amp; <script>alert(1)</script>exposed.",
    "IsVerified": True,
}


class Opener:
    """Answers in turn from a list, and remembers every request. Never goes online."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.answers.pop(0)


def ok(*items):
    return 200, {"Content-Type": "application/json"}, json.dumps(list(items)).encode()


def provider(*answers, sleeps=None):
    opener = Opener(*answers)
    waits = sleeps if sleeps is not None else []
    return hibp.HibpProvider(KEY, sleep=waits.append, opener=opener), opener


# The HIBP provider ----------------------------------------------------------------------------


def test_a_request_names_the_app_sends_the_key_and_encodes_the_address():
    hibp_, opener = provider(ok(ADOBE))
    found = hibp_.check(" Test.Person+x@Example.org ")
    request = opener.requests[0]
    assert request.full_url == (
        "https://haveibeenpwned.com/api/v3/breachedaccount/"
        "Test.Person%2Bx%40Example.org?truncateResponse=false"
    )
    assert request.get_header("Hibp-api-key") == KEY
    assert request.get_header("User-agent").startswith("Stealth/")
    assert "@" not in request.get_header("User-agent")  # nothing about the person
    assert [b.name for b in found] == ["Adobe"]


def test_a_breach_is_parsed_and_its_description_is_plain_text():
    (found,) = hibp.parse(ok(ADOBE)[2])
    assert found.breach_date == date(2013, 10, 4)
    assert found.data_classes[2] == "Passwords"
    assert found.description == (
        "In October 2013, 153 million Adobe accounts were breached & alert(1)exposed."
    )
    assert "<" not in found.description


def test_not_found_means_no_breaches():
    hibp_, _ = provider((404, {}, b""))
    assert hibp_.check("test.person@example.org") == []


def test_a_refused_key_is_its_own_error():
    hibp_, _ = provider((401, {}, b"Access denied"))
    with pytest.raises(BreachKeyError):
        hibp_.check("test.person@example.org")


def test_too_many_requests_waits_as_asked_then_tries_again():
    sleeps = []
    hibp_, opener = provider((429, {"retry-after": "3"}, b""), (429, {}, b""), ok(), sleeps=sleeps)
    assert hibp_.check("test.person@example.org") == []
    assert sleeps == [3.0, 2.0]
    assert len(opener.requests) == 3


def test_a_huge_wait_is_capped_and_it_gives_up_eventually():
    sleeps = []
    answers = [(429, {"Retry-After": "86400"}, b"")] * 3
    hibp_, _ = provider(*answers, sleeps=sleeps)
    with pytest.raises(BreachCheckError, match="too often"):
        hibp_.check("test.person@example.org")
    assert sleeps == [hibp.MAX_WAIT, hibp.MAX_WAIT]


@pytest.mark.parametrize("status", [400, 403, 500, 503])
def test_other_answers_are_errors(status):
    hibp_, _ = provider((status, {}, b"<html>nope</html>"))
    with pytest.raises(BreachCheckError):
        hibp_.check("test.person@example.org")


@pytest.mark.parametrize("body", [b"<html>", b'{"Name": "x"}', b"\xff\xfe"])
def test_an_answer_it_cant_read_is_an_error(body):
    with pytest.raises(BreachCheckError):
        hibp.parse(body)


def test_odd_items_are_skipped_and_missing_fields_are_tolerated():
    found = hibp.parse(json.dumps([{"Title": "no name"}, "x", {"Name": "Bare"}]).encode())
    assert found == [Breach(name="Bare", title="Bare")]


def test_no_test_can_reach_hibp_by_accident():
    with pytest.raises(AssertionError, match="tried to reach"):
        hibp.HibpProvider(KEY).check("test.person@example.org")


def test_html_to_text():
    assert html_to_text("<p>One &lt;two&gt;</p>\n<b>three</b>") == "One <two> three"
    assert html_to_text("") == ""


# Manual breaches ------------------------------------------------------------------------------


def test_a_manual_breach_gets_a_stable_name():
    breach = manual_breach("  Old  Forum! ", breach_date=None, exposed="Passwords, , Names")
    assert breach.name == "manual-old-forum"
    assert breach.title == "Old Forum!"
    assert breach.data_classes == ("Passwords", "Names")
    with pytest.raises(StealthError):
        manual_breach("   ", breach_date=None, exposed="")
    with pytest.raises(StealthError):
        manual_breach("x" * 121, breach_date=None, exposed="")


def test_the_manual_provider_answers_only_for_its_address():
    breach = manual_breach("Old Forum", breach_date=None, exposed="")
    manual = ManualProvider({"test.person@example.org": [breach]})
    assert manual.check("Test.Person@example.org ") == [breach]
    assert manual.check("other@example.org") == []


# The key --------------------------------------------------------------------------------------


def test_the_key_goes_to_the_keychain_only(profile):
    assert not breaches.has_key(profile)
    breaches.save_key(profile, " 0123456789ABCDEF 0123456789abcdef\n")
    assert profile.secrets.get(f"hibp-api-key:{profile.id}") == KEY
    assert breaches.has_key(profile)
    rows = profile.conn.execute("SELECT key, value FROM setting").fetchall()
    assert all(KEY not in r["value"] for r in rows)
    breaches.forget_key(profile)
    assert not breaches.has_key(profile)


@pytest.mark.parametrize("key", ["", "abc", "g" * 32, "0" * 33])
def test_a_key_that_isnt_one_is_refused(profile, key):
    with pytest.raises(ProfileError):
        breaches.save_key(profile, key)
    assert not breaches.has_key(profile)


def test_deleting_a_profile_removes_its_key(store):
    profile_id, _code = store.create()
    with store.open(profile_id) as opened:
        breaches.save_key(opened, KEY)
    store.delete(profile_id)
    assert store.secrets.get(f"hibp-api-key:{profile_id}") is None


# Settings and the cycle -----------------------------------------------------------------------


def test_off_by_default(profile):
    settings = profile.settings()
    assert settings.breach_monitoring is False
    assert settings.breach_provider == "manual"
    breaches.save_key(profile, KEY)
    assert not breaches.due(profile, NOON)


def test_due_only_when_on_with_hibp_and_a_key_and_the_time_has_come(profile):
    breaches.configure(profile, on=True, provider="hibp", every_days=30)
    assert not breaches.due(profile, NOON)  # no key yet
    breaches.save_key(profile, KEY)
    assert breaches.due(profile, NOON)
    profile.save_settings(last_breach_check=NOON.isoformat())
    assert not breaches.due(profile, NOON + timedelta(days=29))
    assert breaches.due(profile, NOON + timedelta(days=30))
    breaches.configure(profile, on=True, provider="manual", every_days=30)
    assert not breaches.due(profile, NOON + timedelta(days=30))


def test_bad_settings_are_refused(profile):
    with pytest.raises(ProfileError):
        breaches.configure(profile, on=True, provider="somewhere", every_days=30)
    with pytest.raises(ProfileError):
        breaches.configure(profile, on=True, provider="hibp", every_days=1)


# Recording, alerts and the score --------------------------------------------------------------


def adobe():
    return hibp.parse(ok(ADOBE)[2])


def test_a_check_records_each_breach_once_with_one_alert(profile):
    clock.set_time(NOON)
    fake = FakeProvider({"test.person@example.org": adobe()})
    summary = breaches.run_check(profile, fake, sleep=lambda s: None)
    assert summary.line == "Checked 2 addresses: 1 new breach found."
    assert fake.checked == ["test.person@example.org", "old.address@example.org"]
    (alert,) = alerts.open_alerts(profile, breaches.TOPIC)
    assert alert.title == "Your details were in the Adobe data breach"
    assert "4 October 2013" in alert.message and "passwords" in alert.message
    assert alert.link.startswith("/protect#breach-")
    assert profile.settings().last_breach_check == NOON.isoformat()

    again = breaches.run_check(profile, fake, sleep=lambda s: None)
    assert again.line == "Checked 2 addresses: no new breaches."
    assert len(alerts.open_alerts(profile, breaches.TOPIC)) == 1
    assert len(breaches.all_found(profile)) == 1


def test_hibp_checks_are_spaced_out(profile):
    sleeps = []
    hibp_, opener = provider((404, {}, b""), (404, {}, b""), sleeps=[])
    breaches.run_check(profile, hibp_, sleep=sleeps.append)
    assert sleeps == [hibp.PAUSE_SECONDS]
    assert len(opener.requests) == 2


def test_an_error_keeps_what_was_found_but_doesnt_use_up_the_cycle(profile):
    hibp_, _ = provider(ok(ADOBE), (503, {}, b""))
    with pytest.raises(BreachCheckError):
        breaches.run_check(profile, hibp_, sleep=lambda s: None)
    assert len(breaches.all_found(profile)) == 1
    assert profile.settings().last_breach_check is None


def test_no_addresses_to_check(profile):
    profile.save_person(profile.person().__class__(display_name="Sam", names=["Sam"]))
    with pytest.raises(ProfileError, match="no email addresses"):
        breaches.run_check(profile, FakeProvider(), sleep=lambda s: None)


def test_the_same_breach_on_two_addresses_is_two_entries(profile):
    fake = FakeProvider({a: adobe() for a in breaches.addresses(profile)})
    breaches.run_check(profile, fake, sleep=lambda s: None)
    assert breaches.open_count(profile) == 2


def test_dealing_with_a_breach_clears_its_alert_and_stops_it_counting(profile):
    from stealth_app import views

    (found,) = breaches.record(profile, "test.person@example.org", adobe(), "hibp")
    assert views.exposure(profile).parts[-1].count == 1
    assert views.exposure(profile).value == 95
    breaches.mark_dealt_with(profile, found.id, True)
    assert breaches.get(profile, found.id).dealt_with
    assert alerts.open_alerts(profile, breaches.TOPIC) == []
    assert views.exposure(profile).parts[-1].count == 0
    breaches.mark_dealt_with(profile, found.id, False)
    assert breaches.open_count(profile) == 1
    with pytest.raises(ProfileError):
        breaches.mark_dealt_with(profile, 999, True)


def test_open_breaches_come_first_newest_first(profile):
    old = Breach("Old", "Old", breach_date=date(2012, 1, 1))
    new = Breach("New", "New", breach_date=date(2020, 1, 1))
    undated = Breach("Undated", "Undated")
    found = breaches.record(profile, "test.person@example.org", [old, new, undated], "manual")
    breaches.mark_dealt_with(profile, found[1].id, True)
    assert [f.breach.name for f in breaches.all_found(profile)] == ["Old", "Undated", "New"]


# The scheduler --------------------------------------------------------------------------------


from stealth_app.scheduler import Scheduler  # noqa: E402
from stealth_app.workspace import RealWorkspace  # noqa: E402


class Workspace(RealWorkspace):
    def sleep(self, seconds):
        pass


def scheduled(store, person, make):
    profile_id, _code = store.create()
    with store.open(profile_id) as opened:
        opened.save_person(person)
        breaches.configure(opened, on=True, provider="hibp", every_days=30)
        breaches.save_key(opened, KEY)
    keys = []

    def factory(key):
        keys.append(key)
        return make()

    return Scheduler(Workspace(store), now=clock.now, breach_provider=factory), profile_id, keys


def test_the_scheduler_checks_when_due_and_alerts(store, person):
    clock.set_time(NOON)
    fake = FakeProvider({"test.person@example.org": adobe()})
    scheduler, profile_id, keys = scheduled(store, person, lambda: fake)
    scheduler._check_breaches(profile_id, NOON)
    assert keys == [KEY]
    scheduler._check_breaches(profile_id, NOON + timedelta(days=1))
    assert keys == [KEY]  # not due again for a month
    with store.open(profile_id) as opened:
        assert breaches.open_count(opened) == 1
        assert len(alerts.to_notify(opened)) == 1


def test_no_internet_tries_again_soon_without_using_up_the_cycle(store, person):
    answers = [[(503, {}, b"")], [(404, {}, b""), (404, {}, b"")]]

    def offline():  # the first check finds no internet; the next one works
        return hibp.HibpProvider(KEY, opener=Opener(*answers.pop(0)))

    scheduler, profile_id, keys = scheduled(store, person, offline)
    scheduler._check_breaches(profile_id, NOON)
    scheduler._check_breaches(profile_id, NOON + timedelta(minutes=1))
    assert len(keys) == 1  # waiting before it tries again
    with store.open(profile_id) as opened:
        assert opened.settings().last_breach_check is None
    scheduler._check_breaches(profile_id, NOON + timedelta(hours=2))
    assert len(keys) == 2
    with store.open(profile_id) as opened:
        assert opened.settings().last_breach_check is not None


def test_a_refused_key_alerts_once_a_cycle(store, person):
    def refused():
        return hibp.HibpProvider(KEY, opener=Opener((401, {}, b"")))

    scheduler, profile_id, keys = scheduled(store, person, refused)
    scheduler._check_breaches(profile_id, NOON)
    scheduler._check_breaches(profile_id, NOON + timedelta(days=1))
    assert len(keys) == 1
    with store.open(profile_id) as opened:
        (alert,) = alerts.open_alerts(opened, "breach-check:")
        assert alert.title == "Breach checks have stopped"
        assert "didn't accept your API key" in opened.settings().last_breach_result


def test_without_a_provider_the_scheduler_never_checks(store, person):
    scheduler, profile_id, _keys = scheduled(store, person, FakeProvider)
    scheduler.breach_provider = None
    scheduler._check_breaches(profile_id, NOON)
    with store.open(profile_id) as opened:
        assert opened.settings().last_breach_check is None


# The page -------------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from stealth.profiles.models import Person  # noqa: E402
from stealth_app.server import create_app  # noqa: E402

TOKEN = "t" * 43
PORT = 50128
BASE = f"http://127.0.0.1:{PORT}"


class PageWorkspace(RealWorkspace):
    fake = FakeProvider({"sam@example.org": adobe()})

    def breach_provider(self, api_key):
        assert api_key == KEY
        return self.fake

    def sleep(self, seconds):
        pass


@pytest.fixture
def client(store):
    profile_id, _code = store.create()
    with store.open(profile_id) as opened:
        opened.save_person(
            Person(display_name="Sam Sample", names=["Sam Sample"], emails=["sam@example.org"])
        )
    clock.set_time(NOON)
    client = TestClient(create_app(TOKEN, PORT, workspace=PageWorkspace(store)), base_url=BASE)
    client.follow_redirects = False
    assert client.get("/auth", params={"t": TOKEN}).status_code == 303
    return client


def post(client, url, **fields):
    return client.post(url, data={"csrf": client.app.state.dashboard.csrf, **fields})


def card(client):
    html = client.get("/protect").text
    match = re.search(r'<section class="card" id="breaches".*?\n</section>', html, re.S)
    assert match
    return match.group(0)


def test_the_card_starts_off_and_credits_hibp(client):
    html = card(client)
    assert 'data-breach-monitoring="off"' in html
    assert 'href="https://haveibeenpwned.com/" target="_blank" rel="noopener noreferrer"' in html
    assert "CC BY 4.0" in html
    assert "Add a breach you found" in html
    assert 'name="api_key"' not in html  # only once HIBP is chosen


def test_turning_on_hibp_and_saving_a_key(client):
    response = post(client, "/protect/breaches/settings", on="yes", provider="hibp", every="30")
    assert response.headers["location"] == "/protect#breaches"
    html = card(client)
    assert 'data-breach-monitoring="on"' in html
    assert "sends your email addresses to Have" in html
    assert 'type="password"' in html
    assert post(client, "/protect/breaches/key", api_key="nope").status_code == 400
    assert post(client, "/protect/breaches/key", api_key=KEY).status_code == 303
    html = card(client)
    assert "Your API key is saved" in html and "Check now" in html
    assert KEY not in client.get("/protect").text
    post(client, "/protect/breaches/key/forget")
    assert "Your API key is saved" not in card(client)


def test_check_now_runs_as_a_job_and_feeds_the_score(client):
    assert post(client, "/protect/breaches/check").status_code == 400  # no key yet
    post(client, "/protect/breaches/settings", on="yes", provider="hibp", every="30")
    post(client, "/protect/breaches/key", api_key=KEY)
    response = post(client, "/protect/breaches/check")
    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[1]
    job = client.app.state.dashboard.jobs.wait(job_id)
    assert job.state == "done", job.error
    assert "1 new breach found" in " ".join(job.lines)
    html = card(client)
    assert "Adobe" in html and "1 to deal with" in html
    assert "accounts were breached" in html and "<script>" not in html
    assert "Your details were in the Adobe data breach" in client.get("/").text
    found_id = re.search(r'data-breach="(\d+)"', html).group(1)
    post(client, f"/protect/breaches/{found_id}/dealt", dealt="yes")
    assert "1 to deal with" not in card(client)


def test_adding_a_breach_by_hand(client):
    response = post(
        client,
        "/protect/breaches/add",
        email="sam@example.org",
        title="Old Forum",
        date="2019-05-01",
        exposed="Passwords",
    )
    assert response.status_code == 303
    html = card(client)
    assert "Old Forum" in html and "added by you" in html and "1 May 2019" in html
    bad = [
        {"email": "someone.else@example.org", "title": "X", "date": "", "exposed": ""},
        {"email": "sam@example.org", "title": "", "date": "", "exposed": ""},
        {"email": "sam@example.org", "title": "X", "date": "2027-01-01", "exposed": ""},
        {"email": "sam@example.org", "title": "X", "date": "May", "exposed": ""},
    ]
    for fields in bad:
        assert post(client, "/protect/breaches/add", **fields).status_code == 400


def test_bad_settings_are_refused_politely(client):
    assert post(client, "/protect/breaches/settings", provider="x", every="30").status_code == 400
    assert post(client, "/protect/breaches/settings", provider="hibp", every="x").status_code == 400
