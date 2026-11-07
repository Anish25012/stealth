"""Phone view: one code, one phone, read-only, over HTTPS, and it ends by itself."""

import ssl
import urllib.parse
import warnings
from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

import httpx
from cryptography import x509
from fastapi.testclient import TestClient

from stealth.core import clock
from stealth_app import phone
from stealth_app.server import create_app
from stealth_app.workspace import RealWorkspace

IP, PORT = "192.168.1.20", 8443
TOKEN = "t" * 43
DESKTOP = 50125


@pytest.fixture
def desktop(store):
    app = create_app(TOKEN, DESKTOP, workspace=RealWorkspace(store))
    client = TestClient(app, base_url=f"http://127.0.0.1:{DESKTOP}", follow_redirects=False)
    client.get("/auth", params={"t": TOKEN})
    yield client
    state = app.state.dashboard
    if state.phone is not None:
        state.phone.stop()
    if state.demo is not None:
        state.demo.close()


@pytest.fixture
def view():
    return phone.PhoneView(IP, PORT, "123456", clock.now(), "AB:CD")


@pytest.fixture
def handset(desktop, view, profile):
    state = desktop.app.state.dashboard
    app = phone.create_phone_app(view, state)
    return TestClient(app, base_url=f"https://{IP}:{PORT}", follow_redirects=False)


def enter(handset, code):
    return handset.post("/code", data={"code": code})


def test_the_code_opens_phone_view_once(handset, view):
    assert handset.get("/home").headers["location"] == "/"  # not before the code
    assert "Enter the code" in handset.get("/").text
    response = enter(handset, "123 456")  # spaces as shown on screen are fine
    assert response.status_code == 303 and response.headers["location"] == "/home"
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    home = handset.get("/home")
    assert home.status_code == 200
    assert "Test Person" in home.text and "Read-only phone view" in home.text
    assert handset.get("/companies").status_code == 200

    other = TestClient(handset.app, base_url=f"https://{IP}:{PORT}", follow_redirects=False)
    second = enter(other, "123456")
    assert second.status_code == 403 and "Another phone already used this code" in second.text
    assert other.get("/home").status_code == 303


def test_five_wrong_codes_end_phone_view(handset, view):
    for left in (4, 3, 2, 1):
        page = enter(handset, "000000")
        assert page.status_code == 403 and f"{left} tr" in page.text
    assert "Too many wrong codes" in enter(handset, "000000").text
    assert view.stopped
    assert enter(handset, "123456").status_code == 410


def test_phone_view_cant_change_anything(handset):
    enter(handset, "123456")
    for url in ("/cycle", "/approve", "/settings/automation", "/home"):
        assert handset.post(url).status_code == 405


def test_the_wrong_host_is_refused(desktop, view):
    app = phone.create_phone_app(view, desktop.app.state.dashboard)
    rebound = TestClient(app, base_url="https://evil.example:8443")
    assert rebound.get("/").status_code == 421


def test_a_code_from_another_site_is_refused(handset):
    response = handset.post("/code", data={"code": "123456"}, headers={"origin": "https://x.test"})
    assert response.status_code == 403
    assert handset.get("/home").status_code == 303


def test_phone_view_ends_after_its_time_limit(handset):
    enter(handset, "123456")
    clock.set_time(clock.now() + phone.LIFETIME + timedelta(seconds=1))
    response = handset.get("/home")
    assert response.status_code == 410 and "Phone view has ended" in response.text


def test_the_certificate_names_the_address_and_is_reused(tmp_path):
    first = phone.certificate(tmp_path, "192.168.1.20")
    cert = x509.load_pem_x509_certificate(first.cert.read_bytes())
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(a) for a in names.get_values_for_type(x509.IPAddress)] == ["192.168.1.20"]
    assert phone.certificate(tmp_path, "192.168.1.20").fingerprint == first.fingerprint
    assert phone.certificate(tmp_path, "192.168.1.21").fingerprint != first.fingerprint


def test_codes_are_six_digits():
    assert all(len(c) == 6 and c.isdigit() for c in (phone.new_code() for _ in range(50)))


def test_settings_turns_phone_view_on_and_off_over_real_https(desktop, profile, monkeypatch):
    monkeypatch.setattr(phone, "lan_address", lambda: "127.0.0.1")
    csrf = desktop.app.state.dashboard.csrf
    assert desktop.post("/phone/start", data={"csrf": csrf}).status_code == 303
    view = desktop.app.state.dashboard.phone
    settings = desktop.get("/settings").text
    assert view.url in settings and f"{view.code[:3]} {view.code[3:]}" in settings

    # The phone's side, over TLS. It can't trust a home-made certificate, so it checks the
    # fingerprint shown in Settings, the way the person would.
    served = ssl.get_server_certificate(("127.0.0.1", view.port)).encode()
    if (
        x509.load_pem_x509_certificate(served).issuer.rfc4514_string()
        == "CN=Stealth on this computer"
    ):
        assert phone._fingerprint(served) == view.fingerprint
    else:
        # Antivirus that inspects HTTPS (Norton's Web Shield does) re-signs the certificate on
        # this computer; a phone on the Wi-Fi connects directly and sees the real one.
        warnings.warn(
            "HTTPS is being intercepted on this computer; fingerprint not compared", stacklevel=1
        )
    with httpx.Client(base_url=view.url, verify=False, follow_redirects=False) as handset:
        assert "Enter the code" in handset.get("/").text
        assert handset.post("/code", data={"code": view.code}).status_code == 303
        assert "Test Person" in handset.get("/home").text

    assert desktop.post("/phone/stop", data={"csrf": csrf}).status_code == 303
    assert desktop.app.state.dashboard.phone is None and view.stopped
    assert "Turn on phone view" in desktop.get("/settings").text


def test_no_network_says_so(desktop, profile, monkeypatch):
    monkeypatch.setattr(phone, "lan_address", lambda: None)
    page = desktop.post("/phone/start", data={"csrf": desktop.app.state.dashboard.csrf})
    assert page.status_code == 400 and "home network" in page.text


# Dead contacts ----------------------------------------------------------------------------


def test_a_bounced_address_offers_a_github_issue_without_personal_data(desktop, profile):
    from stealth.cases.state import all_cases
    from stealth_app import views

    desktop.post("/demo/start", data={"csrf": desktop.app.state.dashboard.csrf})
    demo = desktop.app.state.dashboard.demo
    with demo.store.open() as demo_profile:
        case = next(c for c in all_cases(demo_profile.conn) if c.contact)
        assert views.dead_contact_link(demo_profile, case) is None
        demo_profile.conn.execute(
            "INSERT INTO broker_contact_flag VALUES (?, ?, 'bounced', '2026-11-01T10:00:00+00:00')",
            (case.broker_id, case.contact),
        )
        person = demo_profile.person()
    page = desktop.get(f"/cases/{case.broker_id}").text
    assert "report it on GitHub" in page
    link = views.dead_contact_link
    with demo.store.open() as demo_profile:
        url = link(demo_profile, case)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert url.startswith("https://github.com/Anish25012/stealth/issues/new?")
    assert query["labels"] == ["dead-contact"]
    assert case.contact in query["body"][0] and case.broker_name in query["title"][0]
    for value in [person.display_name, *person.names, *person.emails, *person.addresses]:
        assert value not in url and urllib.parse.quote(value) not in url
