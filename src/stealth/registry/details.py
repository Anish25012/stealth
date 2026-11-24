"""Which of the person's details a company's letters may carry: the least it needs to find them.

Each letter lists details only so the company can find its records, and asks it not to add any it
doesn't already hold. Even so, a detail a company never had shouldn't be sent to it. So:

- UK companies, and marketing lists, get the full set: they match on postal address.
- Contact and profile scrapers, and people-search sites, get name, email and phone: their
  listings are built around phone numbers, not home addresses.
- Ad tech, location tracking and other data companies outside the UK get name and email: they
  hold online identifiers, not postal records.
- Public-records companies, and any category not listed here, get the full set, as before.
- Extended-tier companies always get name and email only (a decision made earlier).

Date of birth still goes only to companies that get the full set, and only after the person has
opted in to sharing it.
"""

from __future__ import annotations

from stealth.registry.loader import Broker

Details = frozenset[str]

NAME_EMAIL: Details = frozenset({"name", "email"})
WITH_PHONE: Details = frozenset({"name", "email", "phone"})
FULL: Details = frozenset({"name", "email", "phone", "address", "date_of_birth"})

BY_CATEGORY: dict[str, Details] = {
    "data-aggregator": NAME_EMAIL,
    "location-tracking": NAME_EMAIL,
    "other": NAME_EMAIL,
    "social-scraper": WITH_PHONE,
    "people-search": WITH_PHONE,
    "marketing-list": FULL,
    "public-records": FULL,
}


def for_broker(broker: Broker | None, tier: str) -> Details:
    """The details a letter to this company may contain. An unknown company gets the full set,
    as every company did before this rule, so nothing it needs to find the person goes missing."""
    if tier == "extended":
        return NAME_EMAIL
    if broker is None or broker.region == "UK":
        return FULL
    return BY_CATEGORY.get(broker.category, FULL)
