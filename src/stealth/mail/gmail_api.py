"""Sending and reading through the Gmail API, over HTTPS, after signing in with Google.

The engine sees the same Transport and Mailbox as with IMAP and SMTP. Messages are sent and
fetched as complete RFC 822 bytes, so threading headers, case references and the evidence kept
on disk are identical either way.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Any

from stealth.core.errors import MailAuthError, MailConnectionError, MailSendError
from stealth.mail import google_oauth
from stealth.mail.base import IncomingMail

API = "https://gmail.googleapis.com/gmail/v1/users/me"
PAGE_SIZE = 100
MAX_MESSAGES = 2000  # far more than any day's replies; stops a runaway on a huge inbox


class GmailSession:
    """An access token, refreshed when it runs out, shared by the transport and the mailbox."""

    def __init__(
        self,
        client: google_oauth.Client,
        refresh_token: str,
        http: google_oauth.Http = google_oauth.https_request,
    ) -> None:
        self.client = client
        self.refresh_token = refresh_token
        self.http = http
        self._access: str | None = None

    def call(self, method: str, path: str, body: dict[str, object] | None = None) -> dict[str, Any]:
        for attempt in (1, 2):
            if self._access is None:
                self._access = google_oauth.refresh(self.client, self.refresh_token, self.http)
            headers = {"Authorization": f"Bearer {self._access}"}
            data = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(body).encode()
            status, raw = self.http(method, f"{API}/{path}", headers, data)
            if status == 401 and attempt == 1:
                self._access = None  # expired since we fetched it: refresh once and retry
                continue
            return self._result(status, raw)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _result(status: int, raw: bytes) -> dict[str, Any]:
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            parsed = {}
        if 200 <= status < 300:
            return parsed if isinstance(parsed, dict) else {}
        reason = ""
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            reason = str(parsed["error"].get("message", ""))
        if status in (401, 403):
            raise MailAuthError(
                "Google refused Stealth access to your Gmail.",
                hint=f"Sign in with Google again. Google said: {reason or status}.",
            )
        if status == 429:
            raise MailSendError(
                "Gmail says Stealth is sending too much today.",
                hint="Stealth will try the rest tomorrow.",
            )
        raise MailConnectionError(
            "Gmail had a problem.", hint=f"Try again later. Google said: {reason or status}."
        )

    def address(self) -> str:
        """The signed-in account's own address."""
        return str(self.call("GET", "profile")["emailAddress"])


class GmailApiTransport:
    # Gmail files a sent copy in Sent itself, so no BCC to the person is needed.
    wants_bcc = False

    def __init__(self, session: GmailSession) -> None:
        self.session = session

    def send(self, message: EmailMessage, bcc: str | None = None) -> None:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        self.session.call("POST", "messages/send", {"raw": raw})


class GmailApiMailbox:
    """Read-only: lists and fetches, never labels, moves or deletes."""

    def __init__(self, session: GmailSession) -> None:
        self.session = session

    def check(self) -> None:
        self.session.address()

    def fetch_since(self, since: date) -> list[IncomingMail]:
        # Gmail's `after:` is exclusive and by day, so ask from the day before. Inbox and spam,
        # matching what IMAP's INBOX would hold plus replies Gmail misfiled.
        after = (since - timedelta(days=1)).strftime("%Y/%m/%d")
        query = f"after:{after} -in:sent -in:drafts -in:trash"
        found: list[IncomingMail] = []
        page = ""
        while len(found) < MAX_MESSAGES:
            params = {"q": query, "maxResults": str(PAGE_SIZE), "includeSpamTrash": "true"}
            if page:
                params["pageToken"] = page
            listing = self.session.call("GET", f"messages?{urllib.parse.urlencode(params)}")
            for item in listing.get("messages", []):
                if "TRASH" in item.get("labelIds", []):
                    continue
                message = self.session.call("GET", f"messages/{item['id']}?format=raw")
                if "TRASH" in message.get("labelIds", []):
                    continue
                found.append(IncomingMail(raw=base64.urlsafe_b64decode(message["raw"] + "==")))
            page = str(listing.get("nextPageToken", ""))
            if not page:
                break
        return found


def finish_sign_in(
    sign_in: google_oauth.SignIn,
    callback: google_oauth.Callback,
    http: google_oauth.Http = google_oauth.https_request,
) -> tuple[str, str]:
    """(address, refresh token) once Google has answered, after proving Gmail really works.

    The address comes from Gmail itself, so it's the account the person actually signed in to,
    whatever they typed elsewhere.
    """
    if callback.code is None:
        raise google_oauth.explain(callback)
    tokens = google_oauth.exchange(sign_in, callback.code, http)
    session = GmailSession(sign_in.client, tokens.refresh_token, http)
    session._access = tokens.access_token
    return session.address(), tokens.refresh_token
