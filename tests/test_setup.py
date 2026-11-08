"""The setup screens, driven page by page against real local mail servers."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from stealth.cases.engine import drafts_waiting
from stealth.devmail.servers import LocalImap, LocalSmtp
from stealth.profiles import keys
from stealth_app.server import create_app
from stealth_app.workspace import RealWorkspace

TOKEN = "s" * 43
PORT = 50125
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "app-password-1"  # the local test servers' password


@pytest.fixture
def real(store):
    return RealWorkspace(store)


@pytest.fixture
def client(real):
    app = create_app(TOKEN, PORT, workspace=real)
    client = TestClient(app, base_url=BASE, follow_redirects=False)
    assert client.get("/auth", params={"t": TOKEN}).status_code == 303
    yield client
    if app.state.dashboard.demo is not None:
        app.state.dashboard.demo.close()


@pytest.fixture
def mail():
    with LocalSmtp(password=PASSWORD) as smtp, LocalImap(PASSWORD) as imap:
        yield smtp, imap


def post(client, url, **fields):
    return client.post(url, data={"csrf": client.app.state.dashboard.csrf, **fields})


def server_fields(smtp, imap, password=PASSWORD):
    """The "Something else" choice, pointed at the local servers (plain text is allowed only on
    this computer's own address)."""
    return {
        "address": "sam@example.org",
        "provider": "other",
        "password": password,
        "smtp_host": "127.0.0.1",
        "smtp_port": str(smtp.port),
        "smtp_security": "none",
        "imap_host": "127.0.0.1",
        "imap_port": str(imap.port),
        "imap_security": "none",
    }


def through_details(client, **who):
    assert (
        post(client, "/setup/who", **({"for": "me"} | who)).headers["location"] == "/setup/details"
    )
    response = post(
        client,
        "/setup/details",
        name="Sam Sample",
        other_names="Samantha Sample",
        emails="sam@example.org\nsam.old@example.org",
        addresses="2 Test Street, Leeds, LS1 1AA",
    )
    assert response.headers["location"] == "/setup/email"


def test_the_wizard_makes_a_profile_and_drafts_letters_without_sending_any(client, store, mail):
    smtp, imap = mail
    assert client.get("/setup").status_code == 200
    through_details(client)

    response = post(client, "/setup/email", **server_fields(smtp, imap))
    assert response.headers["location"] == "/setup/recovery"
    assert len(store.list_ids()) == 1

    page = client.get("/setup/recovery").text
    code = page.split('class="recovery-code" value="')[1].split('"')[0]
    assert "Tick the box" in post(client, "/setup/recovery").text  # must say it's kept first
    assert post(client, "/setup/recovery", kept="yes").headers["location"] == "/setup/companies"
    assert client.get("/setup/recovery").headers["location"] == "/setup/companies"  # never again

    page = client.get("/setup/companies").text
    offered = [part.split('"')[0] for part in page.split('name="broker" value="')[1:]]
    assert len(offered) > 5
    keep = offered[1:]
    response = client.post(
        "/setup/companies",
        data={"csrf": client.app.state.dashboard.csrf, "broker": keep},
    )
    assert response.headers["location"] == "/setup/finish"
    assert "Write the letters" in client.get("/setup/finish").text

    assert post(client, "/setup/finish").headers["location"] == "/approve"
    with store.open() as profile:
        assert profile.person().display_name == "Sam Sample"
        assert profile.settings().skip_brokers == [offered[0]]
        drafted = {case.broker_id for case in drafts_waiting(profile)}
    assert drafted and offered[0] not in drafted
    assert smtp.delivered == []  # nothing goes out until it's approved
    assert "Waiting for your OK" in client.get("/approve").text

    # The code shown really does unlock the profile's key.
    profile_id = store.active_id()
    assert keys.unwrap_key(code, store.root / profile_id / "key.wrapped")


def test_a_wrong_password_saves_nothing_and_says_so_beside_the_field(client, store, mail):
    smtp, imap = mail
    through_details(client)
    response = post(client, "/setup/email", **server_fields(smtp, imap, password="wrong"))
    assert response.status_code == 200
    assert 'id="e-password"' in response.text and "app password" in response.text
    assert store.list_ids() == []


def test_setting_up_for_someone_else_needs_their_agreement(client, store, mail):
    page = post(client, "/setup/who", **{"for": "someone"}).text
    assert "Enter your own name" in page and "Choose how they agreed" in page
    assert "has agreed to it" in page

    smtp, imap = mail
    through_details(
        client,
        **{
            "for": "someone",
            "helper_name": "Alex Helper",
            "consent_method": "They told me in person",
            "agreed": "yes",
        },
    )
    assert "Whose email is it?" in client.get("/setup/email").text
    post(client, "/setup/email", whose="theirs", **server_fields(smtp, imap))
    with store.open() as profile:
        consent = profile.consents()[0]
    assert consent["given_by"] == "Sam Sample"
    assert "recorded by Alex Helper" in consent["method"]


def test_outlook_addresses_are_told_microsoft_sign_in_is_needed(client, store):
    through_details(client)
    response = post(
        client, "/setup/email", address="sam@outlook.com", provider="gmail", password="x"
    )
    assert "Microsoft sign-in" in response.text
    assert store.list_ids() == []


def test_unprotected_connections_are_refused_except_on_this_computer(client):
    through_details(client)
    fields = server_fields(type("S", (), {"port": 25}), type("I", (), {"port": 143}))
    fields |= {"smtp_host": "mail.example.org", "imap_host": "mail.example.org"}
    response = post(client, "/setup/email", **fields)
    # Both servers, each listed in the summary at the top and again beside its field.
    assert response.text.count("Choose a protected connection") == 4


def test_details_are_checked_before_moving_on(client):
    post(client, "/setup/who", **{"for": "me"})
    page = post(client, "/setup/details", name="", emails="not-an-address").text
    assert "Enter a full name" in page
    assert "doesn&#39;t look like an email address" in page


def test_setup_is_closed_during_the_demo(client):
    post(client, "/demo/start")
    response = client.get("/setup")
    assert response.status_code == 400
    assert "Leave the demo first" in response.text


def test_people_can_be_switched_and_deleted_only_after_typing_delete(client, store, mail):
    smtp, imap = mail
    for name in ("First Person", "Second Person"):
        client.get("/setup")
        post(client, "/setup/who", **{"for": "me"})
        post(client, "/setup/details", name=name, emails="p@example.org")
        post(client, "/setup/email", **server_fields(smtp, imap))
    first, second = store.list_ids()
    assert store.active_id() == second  # the newest is shown

    page = client.get("/people").text
    assert "First Person" in page and "Second Person" in page
    assert post(client, "/people/switch", profile=first).status_code == 303
    assert store.active_id() == first

    response = post(client, "/people/delete", profile=second, confirm="yes")
    assert "Nothing was deleted" in response.text
    assert post(client, "/people/delete", profile=second, confirm="DELETE").status_code == 303
    assert store.list_ids() == [first]


def test_welcome_and_settings_lead_to_setup(client):
    assert 'href="/setup"' in client.get("/").text
    assert 'href="/setup"' in client.get("/settings").text


def test_daily_checks_can_be_turned_off_and_moved(client, store, mail):
    smtp, imap = mail
    client.get("/setup")
    post(client, "/setup/who", **{"for": "me"})
    post(client, "/setup/details", name="Sam Sample", emails="sam@example.org")
    post(client, "/setup/email", **server_fields(smtp, imap))
    page = client.get("/settings").text
    assert "Running by itself" in page and "hasn't checked by itself yet" in page
    assert 'id="f-autostart"' in page and "Only the installed app" in page  # run from source

    assert post(client, "/settings/automation", hour="7").status_code == 303
    with store.open() as profile:
        assert profile.settings().auto_run is False  # the switch wasn't sent: it's off
        assert profile.settings().auto_run_hour == 7
    assert "Daily checks are off" in client.get("/").text

    post(client, "/settings/automation", auto_run="yes", hour="9")
    assert "checks by itself every day at 09:00" in client.get("/").text
    assert "Choose a time" in post(client, "/settings/automation", hour="25").text


def test_the_demo_doesnt_run_by_itself(client):
    post(client, "/demo/start")
    response = post(client, "/settings/automation", auto_run="yes", hour="9")
    assert response.status_code == 400 and "doesn&#39;t run by itself" in response.text


def test_setup_asks_where_they_live_and_uses_that_law(client, store, mail):
    smtp, imap = mail
    post(client, "/setup/who", **{"for": "me"})
    page = client.get("/setup/details").text
    assert '<option value="GB" selected>United Kingdom</option>' in page
    assert 'value="CH"' in page and 'value="US-CA"' in page

    bad = post(client, "/setup/details", name="Sam", emails="sam@example.org", country="ZZ")
    assert "Choose where they live from the list." in bad.text

    response = post(client, "/setup/details", name="Sam", emails="sam@example.org", country="CH")
    assert response.headers["location"] == "/setup/email"
    post(client, "/setup/email", **server_fields(smtp, imap))
    post(client, "/setup/recovery", kept="yes")
    with store.open() as profile:
        assert profile.person().country == "CH"

    companies = client.get("/setup/companies").text
    # Nothing on the list is clearly under Swiss law: say so and point at the wider list.
    assert "None of the companies on Stealth's list are clearly covered" in companies
    assert "people in Switzerland" in companies

    settings = client.get("/settings").text
    assert "Switzerland" in settings and "asked for, not set by law" in settings
    assert "haven't been used for real yet" in settings
    assert "Ready for the FDPIC" in settings and "ICO" not in settings
