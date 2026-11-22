"""Have I Been Pwned (HIBP), API v3, with the person's own API key.

Checking an address sends it to HIBP in the request, which is why breach monitoring is opt-in and
says so. The key lives in the keychain and goes only to haveibeenpwned.com.

What the API does, per https://haveibeenpwned.com/API/v3 (read 22 November 2026):

- GET /api/v3/breachedaccount/{url-encoded address}?truncateResponse=false needs the
  `hibp-api-key` header and a user agent naming the app.
- 200 is a list of breaches; 404 means nothing publicly disclosable was found (not proof the
  address was never breached); 401 is a bad key; 429 is over the rate limit, usually with a
  Retry-After header in seconds; 400 is an address it can't read.
- Descriptions are HTML. The data is CC BY 4.0: show that it came from HIBP, with a link.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import quote

from stealth.breaches.provider import Breach, BreachCheckError, BreachKeyError, html_to_text
from stealth.core import tls
from stealth.custom.policy_fetch import USER_AGENT

API = "https://haveibeenpwned.com/api/v3/breachedaccount/"
TIMEOUT = 20
MAX_BYTES = 5_000_000
RETRIES = 2  # after a "too many requests" answer
MAX_WAIT = 60  # seconds, however long HIBP asks for
# The cheapest plan allows 10 checks a minute. Leave a little room between addresses.
PAUSE_SECONDS = 6.5

Response = tuple[int, Mapping[str, str], bytes]
Opener = Callable[[urllib.request.Request], Response]


def _urlopen(request: urllib.request.Request) -> Response:
    """One HTTPS request. Tests replace this so nothing reaches HIBP."""
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=tls.context()) as reply:  # noqa: S310 - https only
            return reply.status, dict(reply.headers.items()), reply.read(MAX_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BreachCheckError(
            "Couldn't reach Have I Been Pwned.",
            hint="Check your internet connection. Stealth will try again on its next check.",
        ) from exc


def _opener() -> Opener:
    return _urlopen  # looked up at call time, so a test's replacement is used


@dataclass
class HibpProvider:
    api_key: str
    sleep: Callable[[float], None] = time.sleep
    opener: Opener | None = None
    source: str = field(default="hibp", init=False)
    name: str = field(default="Have I Been Pwned", init=False)

    def check(self, email: str) -> list[Breach]:
        url = API + quote(email.strip(), safe="") + "?truncateResponse=false"
        request = urllib.request.Request(  # noqa: S310 - a fixed https address
            url,
            headers={
                "hibp-api-key": self.api_key,
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        open_ = self.opener or _opener()
        for attempt in range(RETRIES + 1):
            status, headers, body = open_(request)
            if status == 200:
                return parse(body)
            if status == 404:
                return []
            if status == 401:
                raise BreachKeyError(
                    "Have I Been Pwned didn't accept your API key.",
                    hint="Check the key on the Protect yourself page, or get a new one from "
                    "Have I Been Pwned.",
                )
            if status == 429 and attempt < RETRIES:
                self.sleep(_retry_after(headers))
                continue
            if status == 429:
                raise BreachCheckError(
                    "Have I Been Pwned says Stealth is checking too often.",
                    hint="Stealth will try again on its next check.",
                )
            if status == 400:
                raise BreachCheckError(
                    "Have I Been Pwned couldn't read one of your email addresses.",
                    hint="Check the addresses in your details have no typos.",
                )
            raise BreachCheckError(
                f"Have I Been Pwned didn't answer properly (error {status}).",
                hint="Stealth will try again on its next check.",
            )
        raise AssertionError("unreachable")  # pragma: no cover


def _retry_after(headers: Mapping[str, str]) -> float:
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), "")
    try:
        seconds = float(value)
    except ValueError:
        seconds = 2.0  # HIBP's edge can leave the header out
    return min(max(seconds, 1.0), MAX_WAIT)


def parse(body: bytes) -> list[Breach]:
    try:
        items: Any = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BreachCheckError("Have I Been Pwned sent an answer Stealth couldn't read.") from exc
    if not isinstance(items, list):
        raise BreachCheckError("Have I Been Pwned sent an answer Stealth couldn't read.")
    found = []
    for item in items:
        if not isinstance(item, dict) or not item.get("Name"):
            continue
        found.append(
            Breach(
                name=str(item["Name"]),
                title=str(item.get("Title") or item["Name"]),
                domain=str(item.get("Domain") or ""),
                breach_date=_date(item.get("BreachDate")),
                data_classes=tuple(str(c) for c in item.get("DataClasses") or ()),
                description=html_to_text(str(item.get("Description") or "")),
                verified=bool(item.get("IsVerified", True)),
            )
        )
    return found


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None
