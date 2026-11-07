"""The dashboard, driven through its pages against the demo and real local mail servers."""

import email
import email.policy
import hashlib
import io
import json
import re
import zipfile
from email.message import EmailMessage

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from stealth.cases.state import Status, all_cases, find_case
from stealth.core.logging_scrub import clear_known_values
from stealth.demo import DRAFTS
from stealth_app import views
from stealth_app.server import create_app
from stealth_app.workspace import RealWorkspace

TOKEN = "t" * 43
PORT = 50124
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(autouse=True)
def _forget_scrub_values():
    yield
    clear_known_values()


@pytest.fixture
def real(store):
    return RealWorkspace(store)


@pytest.fixture
def app(real):
    application = create_app(TOKEN, PORT, workspace=real)
    yield application
    state = application.state.dashboard
    if state.demo is not None:
        state.demo.close()


@pytest.fixture
def client(app):
    client = TestClient(app, base_url=BASE, follow_redirects=False)
    assert client.get("/auth", params={"t": TOKEN}).status_code == 303
    return client


def csrf(client):
    return client.app.state.dashboard.csrf


def post(client, url, **fields):
    data = {"csrf": csrf(client), **fields}
    return client.post(url, data=data)


@pytest.fixture
def demo(client):
    response = post(client, "/demo/start")
    assert response.status_code == 303
    return client.app.state.dashboard.demo


def wait_for_job(client, response):
    assert response.status_code == 303, response.text
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    job = client.app.state.dashboard.jobs.wait(job_id)
    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200
    return job, page


def statuses(demo):
    with demo.store.open() as profile:
        return {c.broker_id: c.status for c in all_cases(profile.conn)}


# Acceptance check -------------------------------------------------------------------------


def test_acceptance_dashboard_loads_lists_demo_cases_and_approves_a_send(client, demo):
    """Phase 2 acceptance: the dashboard loads, lists demo cases and approves a send."""
    overview = client.get("/")
    assert overview.status_code == 200
    assert "Overview" in overview.text and "Alex Example" in overview.text

    listing = client.get("/cases")
    for name in ("Prompt Data Ltd", "Silent Lists Ltd", "Refusing Corp", "New Lists Ltd"):
        assert name in listing.text

    approve = client.get("/approve")
    assert "New Lists Ltd" in approve.text and "People Finder UK" in approve.text
    assert "Article 17" in approve.text  # the letter itself is shown before approval
    before = len(demo.smtp.delivered)

    job, page = wait_for_job(
        client,
        client.post(
            "/approve", data={"csrf": csrf(client), "broker": list(DRAFTS), "action": "approve"}
        ),
    )
    assert job.state == "done", job.error
    assert "Letters sent to: New Lists Ltd, People Finder UK." in page.text
    sent = demo.smtp.delivered[before:]
    assert sorted(d.recipients[0] for d in sent) == [
        "privacy@new-lists.test",
        "privacy@people-finder.test",
    ]
    assert all(statuses(demo)[b] is Status.SENT for b in DRAFTS)
    assert "Nothing to approve" in client.get("/approve").text


# Pages ------------------------------------------------------------------------------------


def test_with_no_profile_the_welcome_page_offers_the_demo(client):
    page = client.get("/")
    assert "Welcome to Stealth" in page.text
    assert 'action="/demo/start"' in page.text
    assert client.get("/cases").status_code == 303


def test_overview_counts_every_group(client, demo):
    page = client.get("/").text
    stat = r'data-stat="(\w+)".*?<span class="stat-number">(\d+)</span>'
    counts = dict(re.findall(stat, page, re.S))
    # Needs you is the letters to approve (2) plus the things only the person can do (2).
    assert counts == {"removed": "2", "progress": "2", "needs": "4", "problems": "1"}


def test_filtering_the_list_by_group(client, demo):
    page = client.get("/cases", params={"group": "done"}).text
    assert "Prompt Data Ltd" in page and "Find-a-Neighbour" in page
    assert "Silent Lists Ltd" not in page


def test_a_late_company_is_marked_late(client, demo):
    page = client.get("/cases").text
    row = re.search(r"Silent Lists Ltd.*?</tr>", page, re.S).group(0)
    assert "Late" in row


def test_case_page_shows_history_and_the_choices_that_apply(client, demo):
    page = client.get("/cases/chatty-data").text
    assert "Reply to read" in page
    assert "Removal request sent" in page and "Reply received" in page
    assert "We are looking into this" in page  # the reply sits beside the question about it
    assert 'value="removed"' in page and 'value="refused"' in page
    assert 'value="escalated"' not in page  # not until it's ready for the ICO


def test_saving_what_a_reply_meant(client, demo):
    response = post(client, "/cases/chatty-data/outcome", outcome="removed")
    assert response.status_code == 303
    assert statuses(demo)["chatty-data"] is Status.REMOVED


def test_an_outcome_that_does_not_apply_is_refused(client, demo):
    response = post(client, "/cases/prompt-data/outcome", outcome="info_supplied")
    assert response.status_code == 400
    assert "Choose one of the answers listed" in response.text
    assert statuses(demo)["prompt-data"] is Status.REMOVED


def test_unknown_company_gets_a_friendly_page(client, demo):
    response = client.get("/cases/no-such-company")
    assert response.status_code == 400
    assert "Stealth has no case for that company." in response.text


def test_case_urls_need_the_exact_id(client, demo):
    """The command line matches names loosely; a URL must not pick a different company."""
    assert client.get("/cases/silent").status_code == 400


def test_skipping_drafts_from_the_approval_page(client, demo):
    response = client.post(
        "/approve", data={"csrf": csrf(client), "broker": ["new-lists"], "action": "skip"}
    )
    assert response.status_code == 303
    assert statuses(demo)["new-lists"] is Status.SKIPPED
    assert statuses(demo)["people-finder"] is Status.DRAFT


def test_approving_with_nothing_ticked_explains(client, demo):
    response = post(client, "/approve", action="approve")
    assert response.status_code == 400
    assert "No letters were ticked" in response.text


def test_run_cycle_complains_to_late_companies_but_holds_new_letters(client, demo):
    before = len(demo.smtp.delivered)
    job, page = wait_for_job(client, post(client, "/cycle"))
    assert job.state == "done", job.error
    assert "Formal complaints sent to: Silent Lists Ltd." in page.text
    assert "Need you: Form Only Ltd." in page.text
    assert "2 letter(s) are waiting for your OK" in page.text
    recipients = [d.recipients[0] for d in demo.smtp.delivered[before:]]
    assert recipients == ["privacy@silent-lists.test"]  # the drafts stayed unsent
    assert statuses(demo)["new-lists"] is Status.DRAFT


def test_sending_a_complaint_after_a_refusal(client, demo):
    job, _page = wait_for_job(client, post(client, "/cases/refusing-corp/complain"))
    assert job.state == "done", job.error
    assert statuses(demo)["refusing-corp"] is Status.COMPLAINT_SENT
    assert demo.smtp.delivered[-1].recipients[0] == "privacy@refusing-corp.test"


def test_ico_draft_is_refused_until_ready(client, demo):
    response = post(client, "/cases/silent-lists/ico")
    assert response.status_code == 400
    assert "ready for an ICO complaint yet" in response.text


def test_a_second_job_waits_for_the_first(client, demo):
    demo.lock.acquire()
    try:
        response = post(client, "/cycle")
        assert response.status_code == 400
        assert "busy" in response.text
        response = post(client, "/cases/chatty-data/outcome", outcome="removed")
        assert response.status_code == 400
    finally:
        demo.lock.release()


def test_leaving_the_demo_deletes_it(client, demo):
    folder = demo.store.home
    assert folder.exists()
    assert post(client, "/demo/stop").status_code == 303
    assert not folder.exists()
    assert "Welcome to Stealth" in client.get("/").text


PAGES = ("/", "/cases", "/cases/chatty-data", "/approve", "/needs", "/reports", "/settings")


def test_every_page_has_help_and_one_h1(client, demo):
    for url in PAGES:
        page = client.get(url).text
        assert page.count("<h1") == 1, url
        assert "Help with this page" in page, url
        assert 'href="#main"' in page, url  # skip link


# Replies are hostile input ------------------------------------------------------------------


def _add_reply(demo, broker_id, message: EmailMessage):
    """Store a reply as evidence the way the inbox does, without going through IMAP."""
    from stealth.cases import engine

    with demo.store.open() as profile:
        case = find_case(profile.conn, broker_id)
        engine.save_message(
            profile,
            case,
            direction="in",
            kind="reply",
            raw=message.as_bytes(),
            rfc_message_id=f"<{broker_id}-hostile@test>",
            sender=message["From"],
            subject=message["Subject"],
        )
        row = profile.conn.execute("SELECT max(id) FROM message").fetchone()
        return row[0]


def test_html_in_a_reply_is_never_rendered(client, demo):
    message = EmailMessage()
    message["From"] = "privacy@chatty-data.test"
    message["Subject"] = "<script>alert(1)</script>Re: your request"
    message.set_content(
        "<html><head><style>p{}</style><script>steal()</script></head><body>"
        '<p>We have <b>erased</b> your data.</p><img src="https://tracker.test/pixel.gif">'
        "</body></html>",
        subtype="html",
    )
    message_id = _add_reply(demo, "chatty-data", message)
    page = client.get(f"/cases/chatty-data/messages/{message_id}").text
    assert "We have\nerased\nyour data." in page or "We have erased your data." in page
    assert "<script>" not in page and "steal()" not in page
    assert "&lt;script&gt;alert(1)" in page  # the subject is shown, escaped
    assert "tracker.test" not in page
    assert "<b>" not in page


def test_plain_text_replies_are_escaped(client, demo):
    message = EmailMessage()
    message["From"] = "privacy@chatty-data.test"
    message["Subject"] = "Hello"
    message.set_content('Click <a href="https://evil.test">here</a>')
    message_id = _add_reply(demo, "chatty-data", message)
    page = client.get(f"/cases/chatty-data/messages/{message_id}").text
    assert "&lt;a href=" in page
    assert '<a href="https://evil.test">' not in page


def test_a_message_from_another_case_is_not_shown(client, demo):
    with demo.store.open() as profile:
        other = profile.conn.execute(
            "SELECT m.id FROM message m JOIN cases c ON c.id = m.case_id "
            "WHERE c.broker_id = 'prompt-data' LIMIT 1"
        ).fetchone()[0]
    response = client.get(f"/cases/chatty-data/messages/{other}")
    assert response.status_code == 400


def test_a_changed_evidence_file_is_flagged(client, demo):
    with demo.store.open() as profile:
        row = profile.conn.execute(
            "SELECT m.id, m.path FROM message m JOIN cases c ON c.id = m.case_id "
            "WHERE c.broker_id = 'prompt-data' AND m.direction = 'out'"
        ).fetchone()
        (profile.folder / row["path"]).write_bytes(b"Subject: edited\n\ntampered")
    page = client.get(f"/cases/prompt-data/messages/{row['id']}").text
    assert "no longer matches" in page


def test_html_to_text_drops_scripts_and_keeps_words():
    text = views.html_to_text("<p>One</p><script>x()</script><div>Two &amp; three</div>")
    assert text == "One\nTwo & three"


# Evidence --------------------------------------------------------------------------------


def test_evidence_pack_holds_every_email_and_an_intact_log(client, demo):
    response = client.get("/cases/refusing-corp/evidence.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    pack = zipfile.ZipFile(io.BytesIO(response.content))
    names = pack.namelist()
    assert {"README.txt", "manifest.json", "audit-log.json"} <= set(names)
    manifest = json.loads(pack.read("manifest.json"))
    assert {(m["direction"], m["kind"]) for m in manifest} == {("out", "request"), ("in", "reply")}
    for item in manifest:
        assert hashlib.sha256(pack.read(item["file"])).hexdigest() == item["sha256"]
        email.message_from_bytes(pack.read(item["file"]), policy=email.policy.default)
    log = json.loads(pack.read("audit-log.json"))
    assert log["chain_intact"] is True
    assert "Audit log intact: yes" in pack.read("README.txt").decode()


# Security ----------------------------------------------------------------------------------


def _post_routes(app):
    return [
        route.path.replace("{broker_id}", "chatty-data")
        for route in app.routes
        # /show and /quit are for other copies of the app and check the launch token instead.
        if "POST" in getattr(route, "methods", set()) and route.path not in {"/show", "/quit"}
    ]


def test_every_post_route_needs_the_csrf_token(client, demo):
    routes = _post_routes(client.app)
    assert len(routes) >= 9
    before = statuses(demo)
    for url in routes:
        for data in ({}, {"csrf": "wrong"}):
            response = client.post(url, data={**data, "outcome": "removed", "broker": "new-lists"})
            assert response.status_code == 403, url
            assert "out of date" in response.text, url
    assert statuses(demo) == before
    assert client.app.state.dashboard.demo is demo  # /demo/stop didn't run either


def test_error_pages_keep_the_security_headers(client, demo):
    response = client.get("/cases/no-such-company")
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_an_unexpected_error_shows_scrubbed_details_not_a_traceback(client, demo, monkeypatch):
    def broken(profile):
        raise RuntimeError("database exploded for Alex Example at alex.example@stealth-demo.test")

    monkeypatch.setattr(views, "rows", broken)
    response = client.get("/")
    assert response.status_code == 500
    assert "Something went wrong" in response.text
    assert "Copy details" in response.text
    assert "Traceback" not in response.text
    assert "Alex Example" not in response.text
    assert "alex.example@stealth-demo.test" not in response.text
    assert "RuntimeError: database exploded for [personal] at [email]" in response.text
    assert "test_dashboard.py:" in response.text  # file names, never full paths
    assert "Users" not in response.text.split("Where:")[1]


def test_the_dashboard_has_no_inline_scripts_or_styles(client, demo):
    for url in (*PAGES, "/help"):
        page = client.get(url).text
        assert "<style" not in page and " style=" not in page, url
        assert not re.search(r"<script(?![^>]*\bsrc=)", page), url


def test_quitting_the_app_cleans_up_an_open_demo():
    from stealth_app.server import start_server

    server = start_server()
    state = server.dashboard
    from stealth_app.workspace import DemoWorkspace

    state.demo = DemoWorkspace()
    folder = state.demo.store.home
    server.stop()
    assert state.demo is None
    assert not folder.exists()
