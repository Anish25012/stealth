"""What the law offers in each place, and how long a company has to answer.

Phase 1 ships the UK. Phase 2 adds the others here, each checked against a primary source, because
the deadline a letter states is a legal claim made in the person's name.
"""

from __future__ import annotations

from dataclasses import dataclass

from stealth.core.errors import ProfileError


@dataclass(frozen=True)
class Jurisdiction:
    code: str
    name: str
    tz: str
    regulator: str
    law_tags: frozenset[str]  # registry `laws` values this person can rely on
    deadline_months: int
    deadline_is_statutory: bool
    deadline_source: str
    template_dir: str


UK = Jurisdiction(
    code="GB",
    name="United Kingdom",
    tz="Europe/London",
    regulator="Information Commissioner's Office (ICO)",
    law_tags=frozenset({"uk-gdpr", "gdpr"}),
    deadline_months=1,
    deadline_is_statutory=True,
    deadline_source="UK GDPR Arts 12(3) and 12A, as amended by the Data (Use and Access) Act 2025",
    template_dir="uk",
)

_ALL = {UK.code: UK}


def for_country(country: str) -> Jurisdiction:
    try:
        return _ALL[country.upper()]
    except KeyError:
        raise ProfileError(
            f"Stealth doesn't support {country} yet.",
            hint="Supported for now: " + ", ".join(sorted(_ALL)),
        ) from None
