"""Companies the person added, kept in their own profile and treated like any other company.

A custom company becomes a normal registry entry for that person: it gets a letter (after their OK
in the desktop app), deadlines, complaints, repeat requests and an evidence pack, the same as a
company on Stealth's list. It's always "core": the person asked for it by name.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.core.jurisdictions import for_country
from stealth.custom.email_extract import EMAIL_RE, Candidate, registrable
from stealth.custom.policy_fetch import Lookup
from stealth.profiles.store import Profile
from stealth.registry.loader import Broker

PREFIX = "custom-"
CATEGORIES = {
    "people-search": "A people-search or directory site",
    "marketing-list": "A marketing or mailing list",
    "data-aggregator": "A data broker",
    "other": "Something else",
}


@dataclass(frozen=True)
class CustomCompany:
    id: str
    name: str
    domain: str
    email: str | None
    policy_url: str | None
    candidates: list[dict[str, object]]
    category: str
    listing_url: str | None
    added_at: str

    @property
    def ready(self) -> bool:
        return self.email is not None


def company_id(domain: str) -> str:
    return PREFIX + re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")


def is_custom(broker_id: str) -> bool:
    return broker_id.startswith(PREFIX)


def _row(row: object) -> CustomCompany:
    data = dict(row)  # type: ignore[call-overload]
    data["candidates"] = json.loads(data["candidates"])
    return CustomCompany(**data)


def get(profile: Profile, company: str) -> CustomCompany:
    row = profile.conn.execute("SELECT * FROM custom_company WHERE id = ?", (company,)).fetchone()
    if row is None:
        raise ProfileError("Stealth doesn't know that company.")
    return _row(row)


def all_companies(profile: Profile) -> list[CustomCompany]:
    rows = profile.conn.execute("SELECT * FROM custom_company ORDER BY name")
    return [_row(r) for r in rows]


def _candidate(c: Candidate) -> dict[str, object]:
    return {**asdict(c), "reasons": c.reasons}


def save_lookup(
    profile: Profile, lookup: Lookup, *, listing_url: str | None = None, category: str = "other"
) -> CustomCompany:
    """Keep what a lookup found, so the person can pick an address. Nothing is sent yet."""
    company = company_id(lookup.domain)
    existing = profile.conn.execute(
        "SELECT email FROM custom_company WHERE id = ?", (company,)
    ).fetchone()
    if existing is not None and existing["email"]:
        raise ProfileError(f"You've already added {lookup.name}.")
    profile.conn.execute(
        "INSERT OR REPLACE INTO custom_company (id, name, domain, email, policy_url, candidates, "
        "category, listing_url, added_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
        (
            company,
            lookup.name,
            lookup.domain,
            lookup.policy_url,
            json.dumps([_candidate(c) for c in lookup.candidates]),
            category if category in CATEGORIES else "other",
            listing_url,
            clock.now().isoformat(),
        ),
    )
    return get(profile, company)


def valid_address(address: str) -> str:
    address = address.strip().lower()
    if not EMAIL_RE.fullmatch(address):
        raise ProfileError(
            "That isn't an email address.", hint="It should look like privacy@company.co.uk."
        )
    return address


def choose(
    profile: Profile,
    company: str,
    *,
    email: str,
    name: str | None = None,
    category: str | None = None,
    listing_url: str | None = None,
) -> CustomCompany:
    """The person picked (or typed) the address. From now on it's a company like any other."""
    current = get(profile, company)
    address = valid_address(email)
    if listing_url:
        listing_url = listing_url.strip()
        if not listing_url.startswith(("https://", "http://")):
            raise ProfileError("The listing should be a web address starting https://.")
    profile.conn.execute(
        "UPDATE custom_company SET email = ?, name = ?, category = ?, listing_url = ? WHERE id = ?",
        (
            address,
            (name or current.name).strip()[:80] or current.name,
            category if category in CATEGORIES else current.category,
            listing_url or current.listing_url,
            company,
        ),
    )
    return get(profile, company)


def forget(profile: Profile, company: str) -> None:
    """Drop a company that was never written to. Once a letter has gone, the record stays."""
    sent = profile.conn.execute(
        "SELECT 1 FROM cases WHERE broker_id = ? AND status != 'draft'", (company,)
    ).fetchone()
    if sent:
        raise ProfileError("Stealth has already written to them, so their record stays.")
    profile.conn.execute("DELETE FROM custom_company WHERE id = ?", (company,))


def as_brokers(profile: Profile) -> dict[str, Broker]:
    """This person's own companies, in the form the rest of Stealth expects."""
    laws = tuple(sorted(for_country(profile.person().country).law_tags))
    brokers = {}
    for company in all_companies(profile):
        if not company.ready:
            continue
        brokers[company.id] = Broker(
            id=company.id,
            name=company.name,
            domain=company.domain,
            region="UK",
            category=company.category,
            method="email",
            email=company.email,
            form_url=None,
            notes="Added by you",
            source=company.policy_url or "added by you",
            last_verified=company.added_at[:10],
            laws=laws,
            tier="core",
            confidence=1.0,
            reply_domains=tuple({registrable(company.domain)} - {company.domain}),
        )
    return brokers


def with_custom(profile: Profile, brokers: dict[str, Broker]) -> dict[str, Broker]:
    return {**brokers, **as_brokers(profile)}
