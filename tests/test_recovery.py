"""The recovery playbook: its official sources, the incident log, the pages and the PDF."""

import re
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest

from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.models import Person
from stealth.protect import checklist, incidents, recovery

TODAY = date(2026, 11, 23)
NOON = datetime(2026, 11, 23, 12, tzinfo=UTC)


# The playbook's content -----------------------------------------------------------------------


def test_every_link_is_https_and_on_an_official_site():
    for link in recovery.all_links():
        parts = urlsplit(link.url)
        assert parts.scheme == "https", link.url
        assert link.host in recovery.OFFICIAL_HOSTS, link.url
        assert not parts.username and not parts.password, link.url
        assert link.label and link.source, link.url


def test_every_official_site_is_used():
    used = {link.host for link in recovery.all_links()}
    assert used == recovery.OFFICIAL_HOSTS


def test_the_situations_asked_for_are_all_there():
    assert [s.title for s in recovery.SITUATIONS] == [
        "My email or social media account was hacked",
        "My bank card or account was used",
        "Someone opened credit in my name",
        "I gave details to a scammer",
        "My data was in a breach",
    ]
    for situation in recovery.SITUATIONS:
        ids = [a.id for a in situation.actions]
        assert len(ids) == len(set(ids)), situation.id
        assert all(re.fullmatch(r"[a-z0-9-]+", i) for i in ids)
        for action in situation.actions:
            assert action.what and action.why, action.id
            # Every step has somewhere to go or someone to call, except ones that are just advice.
            assert (
                action.links
                or action.phones
                or action.in_app
                or action.id
                in {
                    "statements",
                    "lender",
                    "contacts",
                }
            ), action.id


def test_the_current_uk_services_and_numbers():
    report = recovery.REPORT_FRAUD
    assert report.url == "https://www.reportfraud.police.uk/"
    assert recovery.REPORT_FRAUD_PHONE.number == "0300 123 2040"
    assert recovery.ICO_PHONE.number == "0303 123 1113"
    assert recovery.POLICE_SCOTLAND.number == "101"
    text = " ".join(f"{a.title} {a.what} {a.why}" for s in recovery.SITUATIONS for a in s.actions)
    assert "report@phishing.gov.uk" in text and "7726" in text
    assert "number on the back of your card" in text
    assert "Action Fraud" not in text  # replaced by Report Fraud on 4 December 2025
    hosts = {link.host for link in recovery.all_links()}
    for host in ("www.experian.co.uk", "www.equifax.co.uk", "www.transunion.co.uk"):
        assert host in hosts
    assert "www.cifas.org.uk" in hosts and "ico.org.uk" in hosts and "www.ncsc.gov.uk" in hosts


def test_every_phone_number_is_confirmed_and_looks_like_one():
    phones = {p for s in recovery.SITUATIONS for a in s.actions for p in a.phones}
    for phone in phones:
        assert re.fullmatch(r"\d{3}|\d{4} \d{3} \d{4}", phone.number), phone.number
        assert phone.label and phone.source


def test_the_links_were_checked_recently():
    assert date.fromisoformat(recovery.CHECKED_ON) <= clock.local_date("Europe/London")
    assert recovery.CHECKED_ON >= checklist.CHECKED_ON


def test_in_app_links_stay_in_stealth():
    for s in recovery.SITUATIONS:
        for a in s.actions:
            for _label, url in a.in_app:
                assert url.startswith("/") and not url.startswith("//"), url


def test_urgent_steps_come_first_in_the_recommended_order():
    for situation in recovery.SITUATIONS:
        urgent = [a.urgent for a in situation.actions]
        assert urgent == sorted(urgent, reverse=True), situation.id  # all urgent ones lead
    assert recovery.BANK.urgent and recovery.LENDER.urgent and recovery.RECOVER.urgent
    assert recovery.REPORT.title == "Report it to the police (Report Fraud)"
    credit = [a.id for a in recovery.BY_ID["credit-opened"].actions]
    assert credit[:2] == ["lender", "bank"]


# The incident log -----------------------------------------------------------------------------


def test_starting_an_incident_logs_it(profile):
    clock.set_time(NOON)
    incident_id = incidents.start(profile, "card-used", date(2026, 11, 20), TODAY)
    incident = incidents.get(profile, incident_id)
    assert incident.situation.id == "card-used"
    assert incident.started_on == date(2026, 11, 20)
    assert (incident.done, incident.total) == (0, 6)
    assert incident.next_action.action.id == "bank"
    assert [e.text for e in incident.entries] == ["Started: My bank card or account was used."]


def test_bad_starts_are_refused(profile):
    with pytest.raises(ProfileError):
        incidents.start(profile, "nonsense", TODAY, TODAY)
    with pytest.raises(ProfileError):
        incidents.start(profile, "card-used", date(2026, 11, 24), TODAY)
    with pytest.raises(ProfileError):
        incidents.start(profile, "card-used", date(2010, 1, 1), TODAY)


def test_ticking_steps_records_references_and_logs(profile):
    incident_id = incidents.start(profile, "card-used", TODAY, TODAY)
    incidents.save_action(
        profile,
        incident_id,
        "bank",
        done=True,
        reference=" FR-123 ",
        note="Card stopped.",
        today=TODAY,
    )
    incident = incidents.get(profile, incident_id)
    bank = incident.progress[0]
    assert bank.done_on == TODAY and bank.reference == "FR-123" and bank.note == "Card stopped."
    assert incident.next_action.action.id == "statements"
    assert incident.entries[-1].text == "Done: Call your bank's fraud line now."
    assert incident.entries[-1].reference == "FR-123"

    # Saving again later keeps the original date and doesn't log twice.
    later = date(2026, 11, 25)
    incidents.save_action(
        profile, incident_id, "bank", done=True, reference="FR-123", note="", today=later
    )
    incident = incidents.get(profile, incident_id)
    assert incident.progress[0].done_on == TODAY
    assert len(incident.entries) == 2

    incidents.save_action(
        profile, incident_id, "bank", done=False, reference="", note="", today=later
    )
    assert incidents.get(profile, incident_id).entries[-1].text.startswith("Marked not done")

    incidents.save_action(
        profile, incident_id, "report-fraud", done=False, reference="NFRC123", note="", today=later
    )
    assert incidents.get(profile, incident_id).entries[-1].text == (
        "Reference noted: Report it to the police (Report Fraud)."
    )


def test_undone_urgent_steps_show_first_but_the_order_itself_never_changes(profile):
    incident_id = incidents.start(profile, "credit-opened", TODAY, TODAY)
    order = ["lender", "bank", "credit-reports", "report-fraud", "cifas", "follow-up-scams"]

    def ids(steps):
        return [p.action.id for p in steps]

    for action in ("credit-reports", "report-fraud"):
        incidents.save_action(
            profile, incident_id, action, done=True, reference="", note="", today=TODAY
        )
    incident = incidents.get(profile, incident_id)
    assert ids(incident.progress) == order  # the recommended order, as in the PDF
    assert ids(incident.on_screen) == order  # urgent and undone are already on top
    assert incident.next_action.action.id == "lender"

    incidents.save_action(
        profile, incident_id, "lender", done=True, reference="", note="", today=TODAY
    )
    incident = incidents.get(profile, incident_id)
    assert ids(incident.progress) == order
    assert ids(incident.on_screen) == [
        "bank",
        "lender",
        "credit-reports",
        "report-fraud",
        "cifas",
        "follow-up-scams",
    ]
    assert incident.next_action.action.id == "bank"

    incidents.save_action(
        profile, incident_id, "bank", done=True, reference="", note="", today=TODAY
    )
    incident = incidents.get(profile, incident_id)
    assert ids(incident.on_screen) == order  # nothing urgent left: back to the playbook's order
    assert incident.next_action.action.id == "cifas"


def test_steps_from_another_situation_and_long_text_are_refused(profile):
    incident_id = incidents.start(profile, "breach", TODAY, TODAY)
    with pytest.raises(ProfileError):
        incidents.save_action(
            profile, incident_id, "bank", done=True, reference="", note="", today=TODAY
        )
    with pytest.raises(ProfileError):
        incidents.save_action(
            profile, incident_id, "passwords", done=True, reference="x" * 201, note="", today=TODAY
        )
    with pytest.raises(ProfileError):
        incidents.get(profile, 999)


def test_the_log_takes_dated_entries_in_order(profile):
    incident_id = incidents.start(profile, "credit-opened", TODAY, TODAY)
    incidents.add_entry(
        profile, incident_id, date(2026, 11, 1), "Letter from a lender.", "L-9", TODAY
    )
    entries = incidents.get(profile, incident_id).entries
    assert [e.happened_on for e in entries] == [date(2026, 11, 1), TODAY]
    with pytest.raises(ProfileError):
        incidents.add_entry(profile, incident_id, TODAY, "   ", "", TODAY)
    with pytest.raises(ProfileError):
        incidents.add_entry(profile, incident_id, date(2026, 12, 1), "Later", "", TODAY)
    incidents.delete_entry(profile, incident_id, entries[0].id)
    assert len(incidents.get(profile, incident_id).entries) == 1


def test_closing_listing_and_deleting(profile):
    first = incidents.start(profile, "breach", date(2026, 10, 1), TODAY)
    second = incidents.start(profile, "scammer", date(2026, 11, 1), TODAY)
    incidents.close(profile, second, TODAY, closed=True)
    assert [i.id for i in incidents.all_incidents(profile)] == [first, second]
    assert incidents.get(profile, second).closed_on == TODAY
    incidents.close(profile, second, TODAY, closed=False)
    assert incidents.get(profile, second).closed_on is None
    incidents.delete(profile, first)
    assert [i.id for i in incidents.all_incidents(profile)] == [second]
    left = profile.conn.execute(
        "SELECT count(*) FROM incident_entry WHERE incident_id = ?", (first,)
    )
    assert left.fetchone()[0] == 0


def test_an_incident_from_a_newer_stealth_is_left_out(profile):
    profile.conn.execute(
        "INSERT INTO incident (situation, started_on, created_at) VALUES ('future', ?, ?)",
        (TODAY.isoformat(), NOON.isoformat()),
    )
    assert incidents.all_incidents(profile) == []


# The PDF --------------------------------------------------------------------------------------


def test_the_pdf_is_a_real_pdf_and_escapes_what_was_typed(profile):
    from stealth_app import recovery_pdf

    incident_id = incidents.start(profile, "scammer", TODAY, TODAY)
    incidents.save_action(
        profile,
        incident_id,
        "bank",
        done=True,
        reference="<b>REF</b> & <font size=90>",
        note="Line one\nline two <br/>",
        today=TODAY,
    )
    incidents.add_entry(profile, incident_id, TODAY, "<para>odd & co</para>", "x", TODAY)
    data = recovery_pdf.render(incidents.get(profile, incident_id), "Sam <Sample>", TODAY)
    assert data.startswith(b"%PDF-")
    assert b"Stealth incident log: I gave details to a scammer" in data


def test_every_pdf_page_says_page_x_of_y_and_how_to_store_it(profile, monkeypatch):
    from stealth_app import recovery_pdf

    drawn = []
    for name in ("drawString", "drawRightString"):
        original = getattr(recovery_pdf.Canvas, name)

        def record(self, x, y, text, *args, _original=original, **kwargs):
            drawn.append(text)
            return _original(self, x, y, text, *args, **kwargs)

        monkeypatch.setattr(recovery_pdf.Canvas, name, record)

    incident_id = incidents.start(profile, "card-used", TODAY, TODAY)
    for n in range(80):  # enough log entries for several pages
        incidents.add_entry(
            profile, incident_id, TODAY, f"Call number {n} with the bank.", "", TODAY
        )
    data = recovery_pdf.render(incidents.get(profile, incident_id), "Sam Sample", TODAY)
    pages = [t for t in drawn if t.startswith("Page ")]
    total = len(pages)
    assert total >= 2
    assert pages == [f"Page {n} of {total}" for n in range(1, total + 1)]
    assert drawn.count("Contains personal information. Store it safely.") == total
    assert data.startswith(b"%PDF-")


def test_the_pdf_lists_steps_in_the_recommended_order(profile, monkeypatch):
    from stealth_app import recovery_pdf

    titles = []
    original = recovery_pdf._p

    def spy(text, style):
        titles.append(text)
        return original(text, style)

    monkeypatch.setattr(recovery_pdf, "_p", spy)
    incident_id = incidents.start(profile, "credit-opened", TODAY, TODAY)
    incidents.save_action(
        profile, incident_id, "cifas", done=True, reference="", note="", today=TODAY
    )
    recovery_pdf.render(incidents.get(profile, incident_id), "Sam Sample", TODAY)
    steps = [a.title for a in recovery.BY_ID["credit-opened"].actions]
    shown = [t for t in titles if t in steps]
    assert shown == steps


# The pages ------------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from stealth_app.server import create_app  # noqa: E402
from stealth_app.workspace import RealWorkspace  # noqa: E402

TOKEN = "t" * 43
PORT = 50129
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


def test_reachable_from_protect_and_the_overview(client):
    for page in ("/", "/protect"):
        assert 'href="/recovery"' in client.get(page).text, page
    html = client.get("/recovery").text
    for situation in recovery.SITUATIONS:
        assert situation.title.replace("'", "&#39;") in html
    assert "call 999" in html


def test_working_through_an_incident(client):
    response = post(client, "/recovery/start", situation="card-used", started="2026-11-22")
    assert response.status_code == 303
    url = response.headers["location"]
    assert re.fullmatch(r"/recovery/\d+", url)
    html = client.get(url).text
    assert "0300 123 2040" in html and 'href="tel:03001232040"' in html
    assert (
        'href="https://www.reportfraud.police.uk/" target="_blank" rel="noopener noreferrer"'
        in html
    )
    assert "Next: Call your bank" in html
    assert html.count("Do this now") == 1  # only the bank step is urgent here
    assert "Report it to the police (Report Fraud)" in html

    saved = post(client, f"{url}/action/bank", done="yes", reference="FR-1", note="Stopped.")
    assert saved.headers["location"] == f"{url}#action-bank"
    html = client.get(url).text
    assert 'data-action="bank" data-done="yes"' in html
    assert 'value="FR-1"' in html and "Next: Go through your statements" in html
    assert "Do this now" not in html

    post(client, f"{url}/log", date="2026-11-23", text="Rang the lender <b>", reference="L-1")
    html = client.get(url).text
    assert "Rang the lender &lt;b&gt;" in html

    pdf = client.get(f"{url}.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"] == "application/pdf"
    assert "attachment" in pdf.headers["content-disposition"]

    post(client, f"{url}/close", closed="yes")
    assert "Marked as sorted" in client.get(url).text
    listing = client.get("/recovery").text
    assert "Sorted" in listing


def test_bad_input_is_refused_politely(client):
    assert post(client, "/recovery/start", situation="card-used", started="soon").status_code == 400
    assert (
        post(client, "/recovery/start", situation="nope", started="2026-11-22").status_code == 400
    )
    url = post(client, "/recovery/start", situation="breach", started="2026-11-22").headers[
        "location"
    ]
    assert post(client, f"{url}/action/bank", done="yes").status_code == 400
    assert post(client, f"{url}/log", date="2026-11-23", text="").status_code == 400
    assert post(client, f"{url}/delete").status_code == 400  # not confirmed
    assert client.get("/recovery/999").status_code == 400


def test_deleting_needs_confirming(client):
    url = post(client, "/recovery/start", situation="breach", started="2026-11-22").headers[
        "location"
    ]
    response = post(client, f"{url}/delete", confirm="yes")
    assert response.headers["location"] == "/recovery"
    assert "Your incident logs" not in client.get("/recovery").text
