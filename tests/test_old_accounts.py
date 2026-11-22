"""Find my old accounts: the classifier's accuracy, the Gmail scan, privacy, choices and pages."""

import http.client
import json
import socket
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime

import pytest

from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.custom.policy_fetch import Page
from stealth.mail import gmail_api, google_oauth
from stealth.oldaccounts import gmail
from stealth.oldaccounts import store as old
from stealth.oldaccounts.classify import Header, classify
from stealth.oldaccounts.deletion import find_deletion_page
from tests.old_accounts_inbox import LOOKALIKES, OWN_DOMAINS, SIGNUPS, inbox

NOON = datetime(2026, 11, 23, 12, tzinfo=UTC)
CLIENT = google_oauth.Client("client-id.apps.googleusercontent.com", "not-really-secret")
GOOGLE_HOSTS = {"gmail.googleapis.com", "oauth2.googleapis.com"}


# Accuracy against the fixture inbox -----------------------------------------------------------


def measure() -> dict[str, float]:
    tp = fp = fn = tn = wrong_company = 0
    for header, answer in inbox():
        got = classify(header, OWN_DOMAINS)
        if answer is None:
            fp += got is not None
            tn += got is None
        elif got is None:
            fn += 1
        elif got.domain == answer:
            tp += 1
        else:
            wrong_company += 1
    precision = tp / (tp + fp + wrong_company)
    recall = tp / (tp + fn + wrong_company)
    return {
        "sign-ups": len(SIGNUPS),
        "lookalikes": len(LOOKALIKES),
        "found": tp,
        "missed": fn,
        "wrong company": wrong_company,
        "false alarms": fp,
        "precision": precision,
        "recall": recall,
        "accuracy": (tp + tn) / (tp + tn + fp + fn + wrong_company),
    }


def test_the_classifier_is_accurate_on_the_fixture_inbox(capsys):
    result = measure()
    with capsys.disabled():  # shown with `pytest -s`, and quoted in the pull request
        print("\nFind my old accounts, fixture inbox:", json.dumps(result, indent=1))
    assert result["sign-ups"] >= 30 and result["lookalikes"] >= 20
    assert result["precision"] >= 0.9
    assert result["recall"] >= 0.9
    assert result["false alarms"] == 0 and result["wrong company"] == 0


def test_names_and_encoded_headers():
    def name(sender, subject="Welcome to it"):
        return classify(Header(sender, subject, date(2020, 1, 1))).name

    assert name("The Notion Team <team@makenotion.com>") == "Notion"
    assert name("Spotify <no-reply@spotify.com>") == "Spotify"
    assert name("no-reply@fittrack.test") == "Fittrack"
    zalando = classify(
        Header(
            "=?UTF-8?B?WmFsYW5kbw==?= <info@service-mail.zalando.co.uk>",
            "=?UTF-8?B?V2VsY29tZSB0byBaYWxhbmRv?=",
            date(2020, 1, 1),
        )
    )
    assert (zalando.domain, zalando.name) == ("zalando.co.uk", "Zalando")


def test_own_domains_and_email_providers_are_never_companies():
    mine = Header("Me <me@example.org>", "Welcome to the team", date(2020, 1, 1))
    assert classify(mine, frozenset({"example.org"})) is None
    assert classify(Header("<x@gmail.com>", "Welcome!", date(2020, 1, 1))) is None


def test_one_entry_per_company_with_its_first_email():
    found = old.companies([h for h, _ in inbox()], OWN_DOMAINS)
    domains = [s.domain for s in found]
    assert len(domains) == len(set(domains))
    spotify = next(s for s in found if s.domain == "spotify.com")
    assert spotify.received_on == date(2014, 1, 6)


# The Gmail source: headers only ---------------------------------------------------------------


class FakeGmail:
    """Google's token endpoint and the Gmail API, answering from the fixture inbox."""

    def __init__(self, messages):
        self.messages = messages  # newest first, like Gmail
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append(url)
        if url == google_oauth.TOKEN_URL:
            return 200, json.dumps({"access_token": "access-0"}).encode()
        assert headers["Authorization"] == "Bearer access-0"
        path, _, query = url.removeprefix(gmail_api.API + "/").partition("?")
        params = urllib.parse.parse_qs(query)
        if path == "messages":
            start = int(params.get("pageToken", ["0"])[0])
            size = int(params["maxResults"][0])
            chosen = self.matching(params["q"][0])
            page = chosen[start : start + size]
            reply = {"messages": [{"id": str(self.messages.index(h))} for h in page]}
            if start + size < len(chosen):
                reply["nextPageToken"] = str(start + size)
            return 200, json.dumps(reply).encode()
        header = self.messages[int(path.removeprefix("messages/"))]
        assert params["format"] == ["metadata"]
        assert params["metadataHeaders"] == ["From", "Subject", "Date"]
        millis = int(datetime.combine(header.received_on, datetime.min.time(), UTC).timestamp())
        return 200, json.dumps(
            {
                "internalDate": str(millis * 1000),
                "payload": {
                    "headers": [
                        {"name": "From", "value": header.sender},
                        {"name": "Subject", "value": header.subject},
                    ]
                },
            }
        ).encode()

    def matching(self, q):
        before = None
        for part in q.split():
            if part.startswith("before:"):
                before = datetime.strptime(part[7:], "%Y/%m/%d").date()
        return [h for h in self.messages if before is None or h.received_on < before]


def newest_first():
    return sorted((h for h, _ in inbox()), key=lambda h: h.received_on, reverse=True)


def source(messages, **kwargs):
    fake = FakeGmail(messages)
    return gmail.GmailSignups(gmail_api.GmailSession(CLIENT, "refresh-1", fake)), fake


def test_the_search_asks_for_sign_up_subjects_and_skips_sent_mail():
    q = gmail.query(None)
    assert 'subject:"welcome"' in q and 'subject:"verify"' in q
    assert "-in:sent" in q and "-in:drafts" in q and "before:" not in q
    assert gmail.query(date(2020, 3, 9)).endswith("before:2020/03/10")


def test_only_three_headers_are_ever_asked_for():
    signups, fake = source(newest_first())
    batch = signups.fetch(None)
    assert len(batch.headers) == len(newest_first()) and not batch.more
    for url in fake.requests:
        assert "format=raw" not in url and "format=full" not in url
        if "/messages/" in url:
            assert "format=metadata" in url


def test_a_batch_stops_at_its_limit_and_says_there_is_more():
    signups, _ = source(newest_first())
    batch = signups.fetch(None, limit=10)
    assert len(batch.headers) == 10 and batch.more
    assert batch.oldest == newest_first()[9].received_on


# Nothing leaves the computer except requests to Gmail -----------------------------------------


@pytest.fixture
def no_network(monkeypatch):
    """Any DNS lookup, HTTP connection or socket leaving this computer fails the test.

    Connections to this computer's own loopback address are allowed: on Windows, Python's event
    loop talks to itself that way. Nothing sent to 127.0.0.1 can leave the computer.
    """
    tried = []
    loopback = {"127.0.0.1", "::1"}
    real_connect = socket.socket.connect

    def refuse(name):
        def blocked(*args, **kwargs):
            tried.append((name, args[:2]))
            raise AssertionError(f"a network call outside Gmail: {name} {args[:2]}")

        return blocked

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] in loopback:
            return real_connect(sock, address)
        tried.append(("connect", address))
        raise AssertionError(f"a network call outside Gmail: {address}")

    monkeypatch.setattr(socket, "create_connection", refuse("create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse("http"))
    monkeypatch.setattr(urllib.request, "urlopen", refuse("urlopen"))
    return tried


def test_a_scan_talks_only_to_gmail(profile, no_network):
    signups, fake = source(newest_first())
    summary = old.run_scan(profile, signups, further_back=False)
    assert summary.signups >= 30
    hosts = {urllib.parse.urlsplit(u).hostname for u in fake.requests}
    assert hosts <= GOOGLE_HOSTS
    assert no_network == []  # every request went through the Gmail session, nothing else


def test_the_real_session_only_knows_google_addresses():
    assert urllib.parse.urlsplit(gmail_api.API).hostname == "gmail.googleapis.com"
    assert urllib.parse.urlsplit(google_oauth.TOKEN_URL).hostname == "oauth2.googleapis.com"


def test_only_company_names_and_dates_are_kept(profile):
    signups, _ = source(newest_first())
    old.run_scan(profile, signups, further_back=False)
    tables = [
        r[0] for r in profile.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    dump = "\n".join(
        repr(tuple(row))
        for table in tables
        for row in profile.conn.execute(f"SELECT * FROM {table}")
    )
    for _sender, subject, _answer in SIGNUPS + LOOKALIKES:
        if len(subject) > 12:
            assert subject not in dump, subject
    assert "no-reply@spotify.com" not in dump


# Choices, re-scans and scanning further back ---------------------------------------------------


def test_choices_are_remembered_and_rescans_only_add_new_companies(profile):
    clock.set_time(NOON)
    first_half = newest_first()[len(newest_first()) // 2 :]  # the older half
    signups, _ = source(first_half)
    first = old.run_scan(profile, signups, further_back=False)
    assert first.new == first.signups > 0
    for account in old.undecided(profile)[:3]:
        old.decide(profile, account.domain, old.KEEP)
    kept = {a.domain for a in old.decided(profile)}

    signups, _ = source(newest_first())
    again = old.run_scan(profile, signups, further_back=False)
    assert again.new == again.signups - first.signups
    undecided = {a.domain for a in old.undecided(profile)}
    assert not (kept & undecided)
    assert {a.domain for a in old.decided(profile)} == kept


def test_scan_further_back_carries_on_from_the_oldest_email(profile, monkeypatch):
    messages = newest_first()
    signups, _ = source(messages)
    monkey_batch = 20
    monkeypatch.setattr(old, "BATCH", monkey_batch)
    first = old.run_scan(profile, signups, further_back=False)
    assert first.more and first.read == monkey_batch
    state = old.scan_state(profile)
    assert state.more and state.oldest_on == messages[monkey_batch - 1].received_on

    second = old.run_scan(profile, signups, further_back=True)
    assert second.oldest < state.oldest_on
    third = old.run_scan(profile, signups, further_back=True)
    assert not third.more
    assert old.scan_state(profile).oldest_on == messages[-1].received_on

    # Scanning recent mail again doesn't forget how far back it has read.
    old.run_scan(profile, signups, further_back=False)
    assert old.scan_state(profile).oldest_on == messages[-1].received_on
    every = {s.domain for s in old.companies(messages, OWN_DOMAINS)}
    assert {a.domain for a in old.undecided(profile)} == every


def test_further_back_needs_a_first_scan(profile):
    with pytest.raises(ProfileError):
        old.run_scan(profile, gmail.FixedSignups(), further_back=True)


def test_bad_choices_and_unknown_companies(profile):
    with pytest.raises(ProfileError):
        old.decide(profile, "nowhere.example", old.KEEP)
    signups, _ = source(newest_first())
    old.run_scan(profile, signups, further_back=False)
    with pytest.raises(ProfileError):
        old.decide(profile, "spotify.com", "maybe")


# The account-deletion page ---------------------------------------------------------------------


def site(pages):
    def fetch(url):
        return Page(url, 200, pages[url]) if url in pages else Page(url, 404, "")

    return fetch


def test_a_deletion_page_on_the_companys_own_site_is_found():
    pages = {
        "https://shop.example/": '<a href="/help">Help</a>',
        "https://shop.example/help": '<a href="https://shop.example/account/close">Close your '
        "account</a>",
    }
    assert find_deletion_page("shop.example", site(pages)) == "https://shop.example/account/close"


def test_links_to_other_sites_and_plain_http_are_never_offered():
    pages = {
        "https://shop.example/": '<a href="https://evil.example/delete-account">Delete your '
        'account</a> <a href="http://shop.example/delete-account">Delete account</a>',
    }
    assert find_deletion_page("shop.example", site(pages)) is None


def test_robots_txt_is_respected():
    pages = {
        "https://shop.example/robots.txt": "User-agent: *\nDisallow: /",
        "https://shop.example/": '<a href="/delete-account">Delete account</a>',
    }
    assert find_deletion_page("shop.example", site(pages)) is None


# The pages -------------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from stealth.profiles.models import GOOGLE_PROVIDER, MailAccount, Person  # noqa: E402
from stealth_app.server import create_app  # noqa: E402
from stealth_app.workspace import RealWorkspace  # noqa: E402

TOKEN = "t" * 43
PORT = 50130
BASE = f"http://127.0.0.1:{PORT}"

DELETION_SITE = {
    "https://spotify.com/": '<a href="/account/delete">Delete your account</a>',
}


class GmailWorkspace(RealWorkspace):
    fake: FakeGmail | None = None

    def signup_source(self, profile):
        self.fake = FakeGmail(newest_first())
        return gmail.GmailSignups(gmail_api.GmailSession(CLIENT, "refresh-1", self.fake))

    def fetch(self, url):
        return site(DELETION_SITE)(url)

    def sleep(self, seconds):
        pass


def make_client(store, provider):
    profile_id, _code = store.create()
    with store.open(profile_id) as opened:
        opened.save_person(
            Person(display_name="Sam Sample", names=["Sam Sample"], emails=["sam@example.org"])
        )
        opened.save_mail_account(
            MailAccount(
                provider=provider,
                address="sam@example.org",
                smtp_host="",
                smtp_port=0,
                smtp_security="",
                imap_host="",
                imap_port=0,
                imap_security="",
                username="sam@example.org",
                is_own_mailbox=True,
            )
        )
    clock.set_time(NOON)
    workspace = GmailWorkspace(store)
    client = TestClient(create_app(TOKEN, PORT, workspace=workspace), base_url=BASE)
    client.follow_redirects = False
    assert client.get("/auth", params={"t": TOKEN}).status_code == 303
    return client, workspace


@pytest.fixture
def client(store):
    return make_client(store, GOOGLE_PROVIDER)[0]


def post(client, url, **fields):
    return client.post(url, data={"csrf": client.app.state.dashboard.csrf, **fields})


def run_job(client, response):
    assert response.status_code == 303, response.text
    job_id = response.headers["location"].rsplit("/", 1)[1]
    job = client.app.state.dashboard.jobs.wait(job_id)
    assert job.state == "done", (job.error, job.hint)
    return job


def test_off_until_asked_and_linked_from_protect(client):
    assert 'href="/old-accounts"' in client.get("/protect").text
    html = client.get("/old-accounts").text
    assert 'data-scanned="no"' in html and "Scan my Gmail" in html
    assert "never the message" in html


def test_app_password_mailboxes_are_told_its_gmail_only(store):
    client, _ = make_client(store, "other")
    html = client.get("/old-accounts").text
    assert "Gmail only for now" in html and "Scan my Gmail" not in html
    assert post(client, "/old-accounts/scan").status_code == 400


def test_scanning_from_the_page_lists_companies_and_talks_only_to_gmail(client, no_network):
    job = run_job(client, post(client, "/old-accounts/scan"))
    assert any("companies you signed up with" in line for line in job.lines)
    html = client.get("/old-accounts").text
    assert 'data-old-account="spotify.com"' in html and "Spotify" in html
    assert "Scan for new sign-ups" in html
    workspace = client.app.state.dashboard.real
    hosts = {urllib.parse.urlsplit(u).hostname for u in workspace.fake.requests}
    assert hosts <= GOOGLE_HOSTS and no_network == []


def test_keep_hides_it_from_the_list_and_change_brings_it_back(client):
    run_job(client, post(client, "/old-accounts/scan"))
    post(client, "/old-accounts/spotify.com/keep")
    html = client.get("/old-accounts").text
    assert 'data-old-account="spotify.com"' not in html
    assert 'data-decided="spotify.com"' in html and "Keeping" in html
    post(client, "/old-accounts/spotify.com/undecide")
    assert 'data-old-account="spotify.com"' in client.get("/old-accounts").text


def test_delete_asks_first_then_starts_the_company_lookup(client):
    run_job(client, post(client, "/old-accounts/scan"))
    confirm = client.get("/old-accounts/strava.com/delete").text
    assert "This will usually close your account with Strava" in confirm
    assert "Nothing is sent until you've read and approved it" in confirm
    assert post(client, "/old-accounts/strava.com/delete").status_code == 400  # not ticked
    assert 'data-old-account="strava.com"' in client.get("/old-accounts").text

    response = post(client, "/old-accounts/strava.com/delete", confirm="yes")
    assert response.status_code == 303 and response.headers["location"].startswith("/jobs/")
    html = client.get("/old-accounts").text
    assert 'data-decided="strava.com"' in html and "Deleting" in html


def test_the_deletion_page_is_offered_next_to_delete_once_found(client):
    run_job(client, post(client, "/old-accounts/scan"))
    html = client.get("/old-accounts").text
    assert "Find their account-deletion page" in html
    job = run_job(client, post(client, "/old-accounts/spotify.com/find-page"))
    assert "Found: https://spotify.com/account/delete" in job.lines[-1]
    html = client.get("/old-accounts").text
    assert (
        'href="https://spotify.com/account/delete" target="_blank" rel="noopener noreferrer"'
        in html
    )
    assert "spotify.com/account/delete" in client.get("/old-accounts/spotify.com/delete").text
    run_job(client, post(client, "/old-accounts/netflix.com/find-page"))
    assert "No account-deletion page found" in client.get("/old-accounts").text


def test_unknown_companies_are_refused_politely(client):
    assert client.get("/old-accounts/nowhere.example/delete").status_code == 400
    assert post(client, "/old-accounts/nowhere.example/keep").status_code == 400


# The scan always stops -------------------------------------------------------------------------


class EndlessPages:
    """A Gmail that never stops offering another page: the same token, or a fresh one each time."""

    def __init__(self, repeat):
        self.repeat = repeat
        self.listings = 0

    def call(self, method, path):
        assert path.startswith("messages?")
        self.listings += 1
        token = "same" if self.repeat else f"page-{self.listings}"
        return {"messages": [], "nextPageToken": token}


@pytest.mark.timeout(10)
def test_a_repeating_page_token_ends_the_scan():
    session = EndlessPages(repeat=True)
    batch = gmail.GmailSignups(session).fetch(None)
    assert session.listings == 2  # the first page, then the repeat is spotted
    assert batch.headers == [] and not batch.more


@pytest.mark.timeout(10)
def test_endless_fresh_page_tokens_stop_at_the_page_limit():
    session = EndlessPages(repeat=False)
    batch = gmail.GmailSignups(session).fetch(None)
    assert session.listings == gmail.max_pages(gmail.BATCH)
    assert batch.more  # pages were left, so Scan further back is still offered


@pytest.mark.timeout(10)
def test_a_listing_never_asks_for_more_than_a_batch(profile):
    signups, fake = source(newest_first())
    signups.fetch(None, limit=5)
    assert sum("/messages/" in u for u in fake.requests) == 5


@pytest.mark.timeout(30)
def test_scan_further_back_gets_past_a_day_with_more_than_a_batch(profile, monkeypatch):
    monkeypatch.setattr(old, "BATCH", 3)
    busy_day = date(2020, 6, 1)
    messages = [Header(f"Shop {n} <hi@shop{n}.example>", "Welcome!", busy_day) for n in range(7)]
    messages += [Header("Gamely <hi@gamely.example>", "Welcome to Gamely", date(2019, 1, 1))]
    signups = gmail.FixedSignups(messages)
    old.run_scan(profile, signups, further_back=False)
    clicks = 0
    while old.scan_state(profile).more:
        clicks += 1
        assert clicks <= 3, "Scan further back is going round in circles"
        before = old.scan_state(profile).oldest_on
        summary = old.run_scan(profile, signups, further_back=True)
        assert summary.oldest < before or not summary.more
    assert "gamely.example" in {a.domain for a in old.undecided(profile)}
