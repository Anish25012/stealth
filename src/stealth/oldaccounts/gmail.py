"""Reading sign-up style messages from Gmail: headers only, read-only.

Gmail does the first sift with a search on the subject line, then Stealth asks for just three
headers of each match (From, Subject, Date) with `format=metadata`. It never asks for a message
body. Every request goes through the signed-in Gmail session, so it reaches only Google.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from stealth.mail.gmail_api import GmailSession
from stealth.oldaccounts.classify import Header

BATCH = 2000  # messages per scan; "Scan further back" carries on from the oldest one reached
PAGE_SIZE = 100
SPARE_PAGES = 5


def max_pages(limit: int) -> int:
    """The most listing requests one batch may make."""
    return -(-limit // PAGE_SIZE) + SPARE_PAGES


SUBJECT_WORDS = (
    "welcome",
    "verify",
    "confirm",
    "activate",
    "signing up",
    "sign up",
    "registering",
    "registration",
    "joining",
    "new account",
    "account created",
)


@dataclass(frozen=True)
class Batch:
    headers: list[Header]
    oldest: date | None  # the oldest message reached, if any
    more: bool  # stopped at the batch size with older matches left


class SignupSource(Protocol):
    def fetch(self, before: date | None, limit: int = BATCH) -> Batch: ...


def query(before: date | None) -> str:
    words = " ".join(f'subject:"{w}"' for w in SUBJECT_WORDS)
    q = f"{{{words}}} -in:sent -in:drafts -in:chats"
    if before is not None:
        # `before:` is by whole day and exclusive: ask up to and including the oldest day reached,
        # so nothing on that day is missed. Repeats are harmless; companies are kept once.
        q += f" before:{(before + timedelta(days=1)).strftime('%Y/%m/%d')}"
    return q


def _headers(message: dict[str, object]) -> Header:
    payload = message.get("payload")
    found = {"from": "", "subject": ""}
    if isinstance(payload, dict):
        for item in payload.get("headers") or []:
            if isinstance(item, dict):
                name = str(item.get("name", "")).lower()
                if name in found:
                    found[name] = str(item.get("value", ""))
    millis = int(str(message.get("internalDate") or 0))
    received = datetime.fromtimestamp(millis / 1000, UTC).date()
    return Header(sender=found["from"], subject=found["subject"], received_on=received)


@dataclass
class GmailSignups:
    session: GmailSession

    def fetch(self, before: date | None, limit: int = BATCH) -> Batch:
        fields = urllib.parse.urlencode(
            [("format", "metadata")] + [("metadataHeaders", h) for h in ("From", "Subject", "Date")]
        )
        headers: list[Header] = []
        seen_pages: set[str] = set()
        page = ""
        more = False
        # A hard stop, whatever Gmail answers: enough pages for a full batch, plus a few spare
        # for pages that come back short. An endless or repeating page token can't keep it going.
        for _ in range(max_pages(limit)):
            params = {"q": query(before), "maxResults": str(PAGE_SIZE)}
            if page:
                params["pageToken"] = page
            listing = self.session.call("GET", f"messages?{urllib.parse.urlencode(params)}")
            for item in listing.get("messages", []):
                if len(headers) >= limit:
                    more = True  # older matches are left for "Scan further back"
                    break
                message = self.session.call("GET", f"messages/{item['id']}?{fields}")
                headers.append(_headers(message))
            page = str(listing.get("nextPageToken", ""))
            if more or not page or page in seen_pages:
                break
            seen_pages.add(page)
        else:
            more = bool(page)  # stopped at the page limit with pages left
        oldest = min((h.received_on for h in headers), default=None)
        return Batch(headers, oldest, more)


@dataclass
class FixedSignups:
    """Made-up messages, for the demo and tests. Newest first, like Gmail."""

    messages: Sequence[Header] = ()
    calls: list[date | None] = field(default_factory=list)

    def fetch(self, before: date | None, limit: int = BATCH) -> Batch:
        self.calls.append(before)
        ordered = sorted(self.messages, key=lambda h: h.received_on, reverse=True)
        if before is not None:
            ordered = [h for h in ordered if h.received_on <= before]
        batch = ordered[:limit]
        oldest = min((h.received_on for h in batch), default=None)
        return Batch(batch, oldest, len(ordered) > limit)
