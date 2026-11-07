"""The exposure score: one number for how much of you is still out there. Higher is better.

It starts at 100 and loses points for what's still exposed, each kind capped so one kind can't
swamp the rest. The formula is shown on screen exactly as written here, so nothing is hidden.

    Listings you found that are still up      -10 each, at most -40
    Companies that refused to remove you       -5 each, at most -25
    Companies that haven't confirmed yet       -1 each, at most -20
    Data breaches (from Phase 3)               -5 each, at most -15

A company counts as "confirmed" once it says it removed you or holds nothing. The score never
claims more than it knows: until you've run a scan, it says listings weren't checked.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from stealth.cases.state import Status

REFUSING = {Status.REFUSED, Status.ICO_READY, Status.ESCALATED}
CONFIRMED = {Status.REMOVED, Status.NO_DATA, Status.SKIPPED}


@dataclass(frozen=True)
class Rule:
    label: str
    each: int
    cap: int


LISTINGS = Rule("Listings you found that are still up", 10, 40)
REFUSED = Rule("Companies that refused to remove you", 5, 25)
WAITING = Rule("Companies that haven't confirmed yet", 1, 20)
BREACHES = Rule("Data breaches", 5, 15)
RULES = (LISTINGS, REFUSED, WAITING, BREACHES)


@dataclass(frozen=True)
class Part:
    rule: Rule
    count: int

    @property
    def points(self) -> int:
        return min(self.count * self.rule.each, self.rule.cap)


@dataclass(frozen=True)
class Score:
    value: int
    parts: tuple[Part, ...]
    listings_checked: bool

    @property
    def band(self) -> str:
        if self.value >= 85:
            return "low"
        if self.value >= 60:
            return "medium"
        return "high"

    @property
    def words(self) -> str:
        return {
            "low": "Little of you is out there",
            "medium": "Some of you is still out there",
            "high": "A lot of you is still out there",
        }[self.band]


def exposure(
    statuses: Iterable[Status], *, open_listings: int, listings_checked: bool, breaches: int = 0
) -> Score:
    statuses = list(statuses)
    parts = (
        Part(LISTINGS, open_listings),
        Part(REFUSED, sum(s in REFUSING for s in statuses)),
        Part(WAITING, sum(s not in REFUSING and s not in CONFIRMED for s in statuses)),
        Part(BREACHES, breaches),
    )
    value = max(0, 100 - sum(p.points for p in parts))
    return Score(value, parts, listings_checked)
