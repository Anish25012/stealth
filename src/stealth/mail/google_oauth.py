"""Signing in with Google, for the Gmail API.

Why: antivirus such as Norton breaks IMAP and SMTP connections but lets HTTPS through, and Google
sign-in means nobody has to make an app password. This follows Google's guidance for desktop apps
(developers.google.com/identity/protocols/oauth2/native-app):

- the person signs in in their own browser, never inside Stealth;
- Google sends them back to a one-off listener on 127.0.0.1 at a random port;
- PKCE (S256) ties the answer to this sign-in, and a random `state` rejects anyone else's;
- the client secret isn't secret. Google treats desktop apps as public clients, so shipping it
  in open-source code gives nobody access to anyone's mail. Only the refresh token does, and that
  lives in the computer's keychain.

Two permissions only: send mail, and read it. Not "modify", so Stealth still can't change or
delete anything in the mailbox, just as with IMAP.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass

from stealth.core import tls
from stealth.core.errors import MailAuthError, MailConnectionError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a password
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = (SEND_SCOPE, READ_SCOPE)
TIMEOUT = 30
SIGN_IN_MINUTES = 5

# The project's Desktop OAuth client, from Google Cloud > Google Auth Platform > Clients. Empty
# until the maintainer creates one; until then "Sign in with Google" isn't offered. The
# environment variables override these, for testing with your own client.
CLIENT_ID = ""
CLIENT_SECRET = ""

Http = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


@dataclass(frozen=True)
class Client:
    id: str
    secret: str


def client() -> Client | None:
    """The OAuth client to use, or None if Google sign-in isn't set up in this build."""
    client_id = os.environ.get("STEALTH_GOOGLE_CLIENT_ID", CLIENT_ID)
    secret = os.environ.get("STEALTH_GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    return Client(client_id, secret) if client_id else None


def https_request(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    """One HTTPS request, verified against the OS trust store. Returns (status, body)."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=tls.context()) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MailConnectionError(
            "Stealth couldn't reach Google.",
            hint="Check your internet connection, then try again.",
        ) from exc


def _form(http_: Http, url: str, fields: dict[str, str]) -> tuple[int, dict[str, object]]:
    body = urllib.parse.urlencode(fields).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    status, data = http_("POST", url, headers, body)
    try:
        parsed = json.loads(data or b"{}")
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def _signed_out() -> MailAuthError:
    return MailAuthError(
        "Stealth is no longer signed in to Google.",
        hint="This happens if you change your Google password, remove Stealth's access in your "
        "Google Account, or don't use Stealth for six months. Sign in with Google again.",
    )


# PKCE -----------------------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = _b64(secrets.token_bytes(48))
    return verifier, _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def auth_url(client_: Client, redirect_uri: str, challenge: str, state: str, hint: str = "") -> str:
    params = {
        "client_id": client_.id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",  # a refresh token, so the daily run works unattended
        "prompt": "consent",  # always return one, even if this account signed in before
    }
    if hint:
        params["login_hint"] = hint
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


# The browser's return trip --------------------------------------------------------------------

_DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>Stealth</title>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;line-height:1.5">
<h1 style="font-size:1.4rem">{heading}</h1><p>{text}</p></body>"""


@dataclass
class Callback:
    """What Google sent back to the listener."""

    code: str | None = None
    error: str | None = None


class _Listener(http.server.HTTPServer):
    expected_state: str
    result: Callback
    arrived: threading.Event


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _Listener

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_response(404)  # a favicon request, or anything else: not the answer
            self.end_headers()
            return
        state = query.get("state", [""])[0]
        if not secrets.compare_digest(state, self.server.expected_state):
            self.server.result = Callback(error="state_mismatch")
        elif "error" in query:
            self.server.result = Callback(error=query["error"][0])
        else:
            self.server.result = Callback(code=query["code"][0])
        ok = self.server.result.code is not None
        page = _DONE_PAGE.format(
            heading="You're signed in" if ok else "Sign-in didn't finish",
            text="You can close this tab and go back to Stealth."
            if ok
            else "Go back to Stealth to see what happened and try again.",
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)
        self.server.arrived.set()

    def log_message(self, format: str, *args: object) -> None:
        """Silent: the query string holds the one-time code."""


@dataclass
class SignIn:
    """One sign-in in progress: open `url` in a browser, then `wait()` for Google's answer."""

    client: Client
    url: str
    redirect_uri: str
    verifier: str
    _listener: _Listener

    def wait(self, timeout: float = SIGN_IN_MINUTES * 60) -> Callback:
        try:
            if not self._listener.arrived.wait(timeout):
                return Callback(error="timeout")
            return self._listener.result
        finally:
            self.close()

    def close(self) -> None:
        self._listener.shutdown()
        self._listener.server_close()


def start(client_: Client, hint: str = "") -> SignIn:
    """Listen on a random local port and build the URL to send the person to."""
    listener = _Listener(("127.0.0.1", 0), _Handler)
    listener.expected_state = secrets.token_urlsafe(24)
    listener.result = Callback(error="timeout")
    listener.arrived = threading.Event()
    threading.Thread(target=listener.serve_forever, name="google-sign-in", daemon=True).start()
    redirect_uri = f"http://127.0.0.1:{listener.server_port}"
    verifier, challenge = pkce_pair()
    url = auth_url(client_, redirect_uri, challenge, listener.expected_state, hint)
    return SignIn(client_, url, redirect_uri, verifier, listener)


def explain(callback: Callback) -> MailAuthError:
    """A message for a sign-in that came back without a code."""
    if callback.error == "access_denied":
        return MailAuthError(
            "Google sign-in was cancelled.",
            hint="Stealth needs your OK to send and read mail. Try again when you're ready.",
        )
    if callback.error == "timeout":
        return MailAuthError(
            f"Google sign-in wasn't finished within {SIGN_IN_MINUTES} minutes.",
            hint="Try again, and finish signing in in the browser window that opens.",
        )
    if callback.error == "state_mismatch":
        return MailAuthError(
            "Stealth ignored a sign-in answer it didn't ask for.",
            hint="Try signing in again.",
        )
    return MailAuthError("Google sign-in didn't work.", hint=f"Google said: {callback.error}.")


# Tokens ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str


def exchange(sign_in: SignIn, code: str, http_: Http = https_request) -> Tokens:
    """Swap the one-time code for tokens, and check both permissions were granted."""
    status, data = _form(
        http_,
        TOKEN_URL,
        {
            "client_id": sign_in.client.id,
            "client_secret": sign_in.client.secret,
            "code": code,
            "code_verifier": sign_in.verifier,
            "grant_type": "authorization_code",
            "redirect_uri": sign_in.redirect_uri,
        },
    )
    if status != 200 or "refresh_token" not in data:
        raise MailAuthError("Google didn't finish the sign-in.", hint="Try signing in again.")
    # Google's consent screen lets people untick individual permissions.
    granted = set(str(data.get("scope", "")).split())
    if not set(SCOPES) <= granted:
        missing = "read" if READ_SCOPE not in granted else "send"
        raise MailAuthError(
            f"Stealth wasn't allowed to {missing} your email.",
            hint="Sign in again and leave both boxes ticked: Stealth needs to send its letters "
            "and read the replies. It can't change or delete anything.",
        )
    return Tokens(str(data["access_token"]), str(data["refresh_token"]))


def refresh(client_: Client, refresh_token: str, http_: Http = https_request) -> str:
    """A fresh access token, valid for about an hour."""
    status, data = _form(
        http_,
        TOKEN_URL,
        {
            "client_id": client_.id,
            "client_secret": client_.secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    if status == 200 and "access_token" in data:
        return str(data["access_token"])
    if data.get("error") == "invalid_grant":
        raise _signed_out()
    raise MailConnectionError(
        "Google wouldn't give Stealth access just now.",
        hint="Try again in a few minutes.",
    )


def revoke(token: str, http_: Http = https_request) -> None:
    """Best effort: tell Google to forget this sign-in."""
    with contextlib.suppress(MailConnectionError):
        _form(http_, REVOKE_URL, {"token": token})


def open_browser(url: str) -> bool:
    return webbrowser.open(url, new=1)
