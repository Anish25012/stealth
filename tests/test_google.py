"""Google sign-in and the Gmail API, against a pretend Google. Nothing here touches the network."""

import base64
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import date

import pytest

from stealth.core.errors import MailAuthError, MailSendError, ProfileError
from stealth.mail import gmail_api, google_oauth
from stealth.mail.base import compose
from stealth.mail.factory import open_mail
from stealth.profiles.models import google_account
from stealth.templates.render import Letter

CLIENT = google_oauth.Client("client-id.apps.googleusercontent.com", "not-really-secret")
BOTH = " ".join(google_oauth.SCOPES)


class FakeGoogle:
    """Answers like Google's token endpoint and Gmail API, and records every request."""

    def __init__(self, scope=BOTH, refresh_ok=True):
        self.scope = scope
        self.refresh_ok = refresh_ok
        self.requests = []
        self.sent = []
        self.inbox = {}  # id -> (raw bytes, labels)
        self.expire_next = False
        self.access = "access-1"

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if url == google_oauth.TOKEN_URL:
            form = urllib.parse.parse_qs(body.decode())
            if form["grant_type"] == ["authorization_code"]:
                return 200, json.dumps(
                    {"access_token": "access-0", "refresh_token": "refresh-1", "scope": self.scope}
                ).encode()
            if not self.refresh_ok:
                return 400, b'{"error": "invalid_grant"}'
            return 200, json.dumps({"access_token": self.access}).encode()
        if headers.get("Authorization") == "Bearer stale" or self.expire_next:
            self.expire_next = False
            return 401, b'{"error": {"message": "expired"}}'
        path = url.removeprefix(gmail_api.API + "/")
        if path == "profile":
            return 200, b'{"emailAddress": "person@gmail.com"}'
        if path == "messages/send":
            self.sent.append(base64.urlsafe_b64decode(json.loads(body)["raw"]))
            return 200, b'{"id": "s1"}'
        if path.startswith("messages?"):
            query = urllib.parse.parse_qs(path.split("?", 1)[1])
            ids = sorted(self.inbox)
            start = int(query.get("pageToken", ["0"])[0])
            chunk = ids[start : start + 2]
            listing = {"messages": [{"id": i} for i in chunk]}
            if start + 2 < len(ids):
                listing["nextPageToken"] = str(start + 2)
            return 200, json.dumps(listing).encode()
        if path.startswith("messages/"):
            raw, labels = self.inbox[path.split("/")[1].split("?")[0]]
            encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
            return 200, json.dumps({"raw": encoded, "labelIds": labels}).encode()
        return 404, b"{}"


# Signing in -----------------------------------------------------------------------------------


def test_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = google_oauth.pkce_pair()
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == digest.decode()
    assert 43 <= len(verifier) <= 128


def test_the_sign_in_url_asks_for_send_and_read_only():
    url = google_oauth.auth_url(CLIENT, "http://127.0.0.1:5000", "chal", "st", hint="a@gmail.com")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["scope"] == [BOTH]
    assert "gmail.modify" not in url
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    assert query["redirect_uri"] == ["http://127.0.0.1:5000"]
    assert query["login_hint"] == ["a@gmail.com"]


def _browser_returns(sign_in, **params):
    url = f"{sign_in.redirect_uri}/?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=5) as page:
        return page.read().decode()


def test_the_listener_takes_googles_answer_once(monkeypatch):
    sign_in = google_oauth.start(CLIENT)
    assert sign_in.redirect_uri.startswith("http://127.0.0.1:")
    state = sign_in._listener.expected_state
    assert f"state={state}" in sign_in.url
    page = _browser_returns(sign_in, code="the-code", state=state)
    assert "You're signed in" in page
    assert sign_in.wait(timeout=5).code == "the-code"


def test_an_answer_with_the_wrong_state_is_refused():
    sign_in = google_oauth.start(CLIENT)
    _browser_returns(sign_in, code="someone-elses", state="guess")
    result = sign_in.wait(timeout=5)
    assert result.code is None
    assert "didn't ask for" in google_oauth.explain(result).message


def test_cancelling_and_timing_out_are_explained():
    sign_in = google_oauth.start(CLIENT)
    _browser_returns(sign_in, error="access_denied", state=sign_in._listener.expected_state)
    assert google_oauth.explain(sign_in.wait(timeout=5)).message == "Google sign-in was cancelled."
    late = google_oauth.start(CLIENT)
    assert "wasn't finished" in google_oauth.explain(late.wait(timeout=0.1)).message


def test_finishing_sign_in_returns_the_real_address_and_refresh_token():
    google = FakeGoogle()
    sign_in = google_oauth.start(CLIENT)
    sign_in.close()
    callback = google_oauth.Callback(code="the-code")
    assert gmail_api.finish_sign_in(sign_in, callback, google) == ("person@gmail.com", "refresh-1")
    exchange = urllib.parse.parse_qs(google.requests[0][3].decode())
    assert exchange["code_verifier"] == [sign_in.verifier]
    assert exchange["redirect_uri"] == [sign_in.redirect_uri]


def test_unticking_a_permission_on_googles_screen_is_explained():
    google = FakeGoogle(scope=google_oauth.SEND_SCOPE)
    sign_in = google_oauth.start(CLIENT)
    sign_in.close()
    with pytest.raises(MailAuthError, match="wasn't allowed to read"):
        gmail_api.finish_sign_in(sign_in, google_oauth.Callback(code="c"), google)


def test_a_revoked_sign_in_says_how_to_fix_it():
    google = FakeGoogle(refresh_ok=False)
    session = gmail_api.GmailSession(CLIENT, "refresh-1", google)
    with pytest.raises(MailAuthError, match="no longer signed in to Google") as error:
        session.address()
    assert "change your Google password" in error.value.hint


def test_the_client_can_come_from_the_environment(monkeypatch):
    monkeypatch.setattr(google_oauth, "CLIENT_ID", "")
    monkeypatch.delenv("STEALTH_GOOGLE_CLIENT_ID", raising=False)
    assert google_oauth.client() is None
    monkeypatch.setenv("STEALTH_GOOGLE_CLIENT_ID", "mine")
    monkeypatch.setenv("STEALTH_GOOGLE_CLIENT_SECRET", "s")
    assert google_oauth.client() == google_oauth.Client("mine", "s")


def test_without_a_secret_google_sign_in_is_not_offered(monkeypatch):
    monkeypatch.setenv("STEALTH_GOOGLE_CLIENT_ID", "mine")
    assert google_oauth.client() is None
    monkeypatch.setattr(google_oauth, "built_in_secret", lambda: "built-in")
    assert google_oauth.client() == google_oauth.Client("mine", "built-in")


# Mail over the API ----------------------------------------------------------------------------


def _letter():
    return Letter(subject="Erasure request [Ref STL-0000ABCD]", body="Please erase my data.")


def test_sending_posts_the_exact_message_and_needs_no_bcc():
    google = FakeGoogle()
    transport = gmail_api.GmailApiTransport(gmail_api.GmailSession(CLIENT, "refresh-1", google))
    message = compose(_letter(), "person@gmail.com", "privacy@broker.test")
    transport.send(message)
    assert transport.wants_bcc is False
    assert google.sent == [message.as_bytes()]
    assert b"STL-0000ABCD" in google.sent[0]


def test_an_expired_access_token_is_refreshed_once():
    google = FakeGoogle()
    session = gmail_api.GmailSession(CLIENT, "refresh-1", google)
    session._access = "stale"
    google.access = "access-2"
    assert session.address() == "person@gmail.com"
    assert google.requests[-1][2]["Authorization"] == "Bearer access-2"


def test_gmail_rate_limits_become_a_send_error():
    def busy(method, url, headers, body):
        if url == google_oauth.TOKEN_URL:
            return 200, b'{"access_token": "a"}'
        return 429, b'{"error": {"message": "rate"}}'

    with pytest.raises(MailSendError):
        gmail_api.GmailSession(CLIENT, "r", busy).address()


def test_reading_pages_through_and_skips_trash():
    google = FakeGoogle()
    for n in range(5):
        google.inbox[f"m{n}"] = (f"Subject: reply {n}\r\n\r\nbody".encode(), ["INBOX"])
    google.inbox["m9"] = (b"Subject: binned\r\n\r\n", ["TRASH"])
    mailbox = gmail_api.GmailApiMailbox(gmail_api.GmailSession(CLIENT, "refresh-1", google))
    found = mailbox.fetch_since(date(2026, 11, 8))
    assert sorted(m.raw.split(b"\r\n")[0] for m in found) == [
        f"Subject: reply {n}".encode() for n in range(5)
    ]
    first_list = next(r[1] for r in google.requests if "messages?" in r[1])
    query = urllib.parse.parse_qs(first_list.split("?", 1)[1])["q"][0]
    assert query.startswith("after:2026/11/07")
    assert "-in:sent" in query
    assert all(r[0] == "GET" for r in google.requests if r[1].startswith(gmail_api.API))


# Choosing the mail route ----------------------------------------------------------------------


def test_a_google_account_opens_gmail_over_https(profile, monkeypatch):
    monkeypatch.setattr(google_oauth, "client", lambda: CLIENT)
    profile.save_mail_account(google_account("person@gmail.com", is_own_mailbox=True), "refresh-1")
    transport, mailbox = open_mail(profile)
    assert isinstance(transport, gmail_api.GmailApiTransport)
    assert isinstance(mailbox, gmail_api.GmailApiMailbox)
    assert transport.session.refresh_token == "refresh-1"


def test_a_google_account_without_a_client_or_token_says_sign_in_again(profile, monkeypatch):
    profile.save_mail_account(google_account("person@gmail.com", is_own_mailbox=True))
    profile.secrets.delete(profile.mail_secret_name)
    monkeypatch.setattr(google_oauth, "client", lambda: CLIENT)
    with pytest.raises(ProfileError, match="isn't signed in to Google"):
        open_mail(profile)
    monkeypatch.setattr(google_oauth, "client", lambda: None)
    profile.save_mail_account(google_account("person@gmail.com", is_own_mailbox=True), "r")
    with pytest.raises(ProfileError, match="isn't signed in to Google"):
        open_mail(profile)
