"""The monthly report: what Stealth did for you in one calendar month, and where things stand.

Everything is worked out from the records Stealth already keeps (the audit log, the letters and
replies, the scan), so a report for any past month can be made again at any time and always says
the same thing. Each company's status at a moment is replayed from its logged status changes, and
the exposure score uses the same formula as the dashboard, so the numbers can't drift apart.

Months follow the person's own calendar (UK time for a UK person), not UTC.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from stealth import score
from stealth.cases.engine import jurisdiction_of
from stealth.cases.state import Status, all_cases
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.store import Profile
from stealth_app.views import STATUS, StatusInfo

MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# What a status change means, said as something that happened. Changes that a letter or reply
# already explains (sent, replied, complaint sent) aren't repeated.
HAPPENED: dict[Status, str] = {
    Status.DRAFT: "New letter written, waiting for your OK",
    Status.AWAITING_FORM: "Asked you to fill in their web form",
    Status.ID_REQUESTED: "Asked for ID or more details",
    Status.EXTENDED: "Took extra time, as the law allows",
    Status.REMOVED: "Confirmed they removed your data",
    Status.NO_DATA: "Said they hold no data about you",
    Status.REFUSED: "Refused to remove you",
    Status.BOUNCED: "Your letter didn't reach them",
    Status.ICO_READY: "Ready to report to the ICO",
    Status.ESCALATED: "Reported to the ICO",
    Status.NEEDS_YOU: "Needs you to look at it",
    Status.SKIPPED: "Skipped, at your request",
}
LETTERS = {"request": "Sent a removal request", "complaint": "Sent a formal complaint"}
CONFIRMED = {Status.REMOVED, Status.NO_DATA}


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    @classmethod
    def parse(cls, key: str) -> Month:
        try:
            year, month = (int(part) for part in key.split("-"))
            return cls(year, month) if 1 <= month <= 12 and 2000 <= year <= 9999 else cls._bad()
        except ValueError:
            return cls._bad()

    @classmethod
    def _bad(cls) -> Month:
        raise ProfileError("That isn't a month Stealth has a report for.")

    @classmethod
    def containing(cls, day: date) -> Month:
        return cls(day.year, day.month)

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        return f"{MONTH_NAMES[self.month - 1]} {self.year}"

    @property
    def name(self) -> str:
        return MONTH_NAMES[self.month - 1]

    def next(self) -> Month:
        return Month(self.year + self.month // 12, self.month % 12 + 1)

    def previous(self) -> Month:
        return Month(self.year - (self.month == 1), (self.month - 2) % 12 + 1)

    def start(self, tz: str) -> datetime:
        """Midnight on the 1st, in the person's time zone, as UTC."""
        return datetime(self.year, self.month, 1, tzinfo=ZoneInfo(tz)).astimezone(UTC)


@dataclass(frozen=True)
class Happening:
    on: date
    text: str


@dataclass(frozen=True)
class CompanyLine:
    name: str
    broker_id: str
    info: StatusInfo  # where it stood at the end of the month
    happenings: list[Happening] = field(default_factory=list)


@dataclass(frozen=True)
class Monthly:
    month: Month
    person: str
    made_on: date
    so_far: bool  # the month isn't over yet
    score_start: score.Score | None  # None when Stealth hadn't started yet
    score_end: score.Score
    letters: int
    complaints: int
    replies: int
    confirmed: int  # companies that removed you or said they held nothing, this month
    listings_found: int
    listings_gone: int
    done: int  # at the end of the month: removed or held nothing
    active: int  # every company except skipped ones
    needs: list[CompanyLine]  # companies waiting on the person at the end of the month
    lines: list[CompanyLine]  # every company, the ones with news this month first

    @property
    def percent(self) -> int:
        return round(100 * self.done / self.active) if self.active else 0

    @property
    def score_change(self) -> int | None:
        return None if self.score_start is None else self.score_end.value - self.score_start.value

    @property
    def headline(self) -> str:
        if not self.active:
            return "Stealth hadn't written to any companies yet."
        done = f"{self.done} of {self.active} companies have removed you or hold nothing"
        if self.confirmed:
            return f"{done}. {self.confirmed} of them confirmed it this month."
        return f"{done}."

    @property
    def quiet(self) -> bool:
        return not any(line.happenings for line in self.lines)


# Replaying the records ---------------------------------------------------------------------


@dataclass
class _History:
    created: datetime
    initial: Status
    changes: list[tuple[datetime, Status, Status]]  # (when, before, after), oldest first


def _when(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _histories(profile: Profile) -> dict[int, _History]:
    current = {c.id: c.status for c in all_cases(profile.conn)}
    found: dict[int, _History] = {}
    rows = profile.conn.execute(
        "SELECT case_id, type, at, payload FROM event "
        "WHERE case_id IS NOT NULL AND type IN ('case_created', 'status_changed') ORDER BY id"
    )
    for row in rows:
        case_id, at = row["case_id"], _when(row["at"])
        if case_id not in current:
            continue
        if row["type"] == "case_created":
            found[case_id] = _History(at, current[case_id], [])
            continue
        payload = json.loads(row["payload"])
        history = found.setdefault(case_id, _History(at, current[case_id], []))
        if not history.changes:
            history.initial = Status(payload["before"])
        history.changes.append((at, Status(payload["before"]), Status(payload["after"])))
    return found


def _status_at(history: _History, moment: datetime) -> Status | None:
    if history.created >= moment:
        return None
    status = history.initial
    for at, _before, after in history.changes:
        if at >= moment:
            break
        status = after
    return status


def _listings(profile: Profile, moment: datetime) -> tuple[int, bool]:
    """Listings still up at `moment`, and whether any site had been checked by then."""
    latest: dict[str, str] = {}
    rows = profile.conn.execute(
        "SELECT site, broker_id, outcome, decided_at FROM scan_result "
        "WHERE outcome != 'pending' AND decided_at IS NOT NULL ORDER BY decided_at, id"
    )
    for row in rows:
        if _when(row["decided_at"]) < moment:
            latest[row["broker_id"] or row["site"]] = row["outcome"]
    return sum(1 for o in latest.values() if o == "listed"), bool(latest)


def _score_at(
    profile: Profile, histories: dict[int, _History], moment: datetime
) -> score.Score | None:
    statuses = [s for h in histories.values() if (s := _status_at(h, moment)) is not None]
    listings, checked = _listings(profile, moment)
    if not statuses and not checked:
        return None
    return score.exposure(statuses, open_listings=listings, listings_checked=checked)


# Building a report -------------------------------------------------------------------------


def _tz(profile: Profile) -> str:
    return jurisdiction_of(profile).tz


def available(profile: Profile) -> list[Month]:
    """Every month from Stealth's first record to this one, newest first."""
    row = profile.conn.execute("SELECT MIN(at) AS first FROM event").fetchone()
    tz = _tz(profile)
    this = Month.containing(clock.local_date(tz))
    if row is None or row["first"] is None:
        return [this]
    month = Month.containing(clock.to_local_date(_when(row["first"]), tz))
    months = []
    while month <= this:
        months.append(month)
        month = month.next()
    return months[::-1]


def last_finished(profile: Profile) -> Month | None:
    """The most recent month that's over and has something in it."""
    months = available(profile)
    return months[1] if len(months) > 1 else None


def build(profile: Profile, month: Month) -> Monthly:
    tz = _tz(profile)
    today = clock.local_date(tz)
    this = Month.containing(today)
    if month > this or month not in available(profile):
        raise ProfileError(
            f"There's no report for {month.label}.",
            hint="Reports start from the month Stealth first wrote to a company.",
        )
    start, end = month.start(tz), month.next().start(tz)
    so_far = month == this
    until = clock.now() if so_far else end
    histories = _histories(profile)
    cases = {c.id: c for c in all_cases(profile.conn)}

    def during(moment: datetime) -> bool:
        return start <= moment < until

    # (when, order, text): on the same moment, a letter or reply comes before what it led to.
    news: dict[int, list[tuple[datetime, int, str]]] = {case_id: [] for case_id in cases}
    confirmed = 0
    for case_id, history in histories.items():
        if during(history.created) and history.initial == Status.DRAFT:
            news[case_id].append((history.created, 2, HAPPENED[Status.DRAFT]))
        for at, before, after in history.changes:
            if not during(at):
                continue
            if before == Status.AWAITING_FORM and after == Status.SENT:
                news[case_id].append((at, 2, "You filled in their web form"))
            elif after in HAPPENED:
                news[case_id].append((at, 2, HAPPENED[after]))
                confirmed += after in CONFIRMED

    letters = complaints = replies = 0
    for row in profile.conn.execute(
        "SELECT case_id, direction, kind, occurred_at FROM message ORDER BY id"
    ):
        at = _when(row["occurred_at"])
        if row["case_id"] not in news or not during(at):
            continue
        if row["direction"] == "in":
            if row["kind"] == "bounce":
                continue  # the status change already says the letter didn't arrive
            replies += 1
            news[row["case_id"]].append((at, 1, "They replied"))
        elif row["kind"] in LETTERS:
            letters += row["kind"] == "request"
            complaints += row["kind"] == "complaint"
            news[row["case_id"]].append((at, 0, LETTERS[row["kind"]]))

    for row in profile.conn.execute(
        "SELECT case_id, at FROM event WHERE type = 'listing_found' ORDER BY id"
    ):
        if row["case_id"] in news and during(_when(row["at"])):
            news[row["case_id"]].append((_when(row["at"]), 1, "You found them listing you"))

    listings_found = listings_gone = 0
    seen: dict[str, str] = {}
    for row in profile.conn.execute(
        "SELECT site, broker_id, outcome, decided_at FROM scan_result "
        "WHERE outcome != 'pending' AND decided_at IS NOT NULL ORDER BY decided_at, id"
    ):
        key, at = row["broker_id"] or row["site"], _when(row["decided_at"])
        if at >= until:
            break
        if at >= start:
            if row["outcome"] == "listed":
                listings_found += 1
            elif seen.get(key) == "listed":
                listings_gone += 1
        seen[key] = row["outcome"]

    lines, needs = [], []
    done = active = 0
    for case_id, case in cases.items():
        found = histories.get(case_id)
        status = _status_at(found, until) if found else case.status
        if status is None:
            continue  # Stealth hadn't started on this company yet
        info = STATUS[status]
        happenings = [
            Happening(clock.to_local_date(at, tz), text)
            for at, _order, text in sorted(news[case_id])
            # A draft is only news if it was still waiting at the end: otherwise it was sent.
            if text != HAPPENED[Status.DRAFT] or status == Status.DRAFT
        ]
        line = CompanyLine(case.broker_name, case.broker_id, info, happenings)
        lines.append(line)
        if status != Status.SKIPPED:
            active += 1
            done += status in CONFIRMED
        if info.group in ("approve", "you", "problem"):
            needs.append(line)
    lines.sort(key=lambda line: (not line.happenings, line.name.lower()))
    needs.sort(key=lambda line: line.name.lower())

    end_score = _score_at(profile, histories, until)
    return Monthly(
        month=month,
        person=profile.person().display_name,
        made_on=today,
        so_far=so_far,
        score_start=_score_at(profile, histories, start),
        score_end=end_score or score.exposure([], open_listings=0, listings_checked=False),
        letters=letters,
        complaints=complaints,
        replies=replies,
        confirmed=confirmed,
        listings_found=listings_found,
        listings_gone=listings_gone,
        done=done,
        active=active,
        needs=needs,
        lines=lines,
    )
