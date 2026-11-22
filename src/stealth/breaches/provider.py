"""What a breach is, and the interface every source of breach data implements.

A provider answers one question: which known breaches is this email address in? Have I Been Pwned
answers it over the internet with the person's own API key (`hibp.HibpProvider`). The manual
provider answers it from what the person found themselves and typed in, so both kinds go through
the same recording, alerts and score. The demo uses a made-up provider and never goes online.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import Protocol

from stealth.core.errors import StealthError


class BreachCheckError(StealthError):
    """A check couldn't be finished. Stealth tries again on a later cycle."""


class BreachKeyError(BreachCheckError):
    """The provider refused the API key. Retrying won't help until the person changes it."""


@dataclass(frozen=True)
class Breach:
    name: str  # the provider's identifier, unique per breach
    title: str
    domain: str = ""
    breach_date: date | None = None
    data_classes: tuple[str, ...] = ()
    description: str = ""  # plain text
    verified: bool = True


class BreachProvider(Protocol):
    source: str  # stored with each breach: "hibp", "manual" or "demo"
    name: str  # shown to the person

    def check(self, email: str) -> list[Breach]:
        """Every known breach `email` is in. Raises BreachCheckError if it can't say."""
        ...


@dataclass
class ManualProvider:
    """Breaches the person found themselves, for example by searching Have I Been Pwned's site."""

    found: Mapping[str, Sequence[Breach]] = field(default_factory=dict)
    source: str = "manual"
    name: str = "added by you"

    def check(self, email: str) -> list[Breach]:
        return list(self.found.get(email.strip().lower(), ()))


@dataclass
class FakeProvider:
    """Made-up answers, for the demo and tests. Never goes online."""

    found: Mapping[str, Sequence[Breach]] = field(default_factory=dict)
    source: str = "demo"
    name: str = "the demo"
    checked: list[str] = field(default_factory=list)

    def check(self, email: str) -> list[Breach]:
        self.checked.append(email)
        return list(self.found.get(email.strip().lower(), ()))


def manual_breach(title: str, *, breach_date: date | None, exposed: str) -> Breach:
    """A breach the person typed in. Its name is made from the title, so re-adding it is a no-op."""
    clean = " ".join(title.split())
    if not clean:
        raise StealthError("Enter the name of the site or company that was breached.")
    if len(clean) > 120 or len(exposed) > 300:
        raise StealthError("Keep the name and what was exposed short.")
    slug = re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-") or "breach"
    classes = tuple(part.strip() for part in exposed.split(",") if part.strip())
    return Breach(name=f"manual-{slug}", title=clean, breach_date=breach_date, data_classes=classes)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """Breach descriptions arrive as HTML. Keep the words only; links and markup are dropped."""
    parser = _Text()
    parser.feed(html or "")
    parser.close()
    return " ".join(unescape("".join(parser.parts)).split())
