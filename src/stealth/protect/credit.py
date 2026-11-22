"""Credit report reminders: check each UK credit reference agency's report on a cycle.

Everyone in the UK can get a free statutory credit report from each agency, as often as they
like. Stealth doesn't fetch reports or watch credit files: it reminds the person to look, records
when they did, and keeps their notes about anything wrong, with the ICO's advice on fixing it.

Every link was checked against its official source on CHECKED_ON, like the checklist's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from stealth import alerts
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.store import Profile
from stealth.protect.checklist import Link

CHECKED_ON = "2026-11-22"
TOPIC = "credit:"
NOTE_LIMIT = 2000

OFFICIAL_HOSTS = frozenset(
    {
        "www.experian.co.uk",
        "www.equifax.co.uk",
        "www.transunion.co.uk",
        "ico.org.uk",
    }
)

# How often to be reminded, in days, with the words shown.
EVERY_CHOICES: tuple[tuple[int, str], ...] = (
    (91, "Every three months"),
    (182, "Every six months"),
    (365, "Once a year"),
)
DEFAULT_EVERY = 182


@dataclass(frozen=True)
class Agency:
    id: str
    name: str
    report: Link


AGENCIES: tuple[Agency, ...] = (
    Agency(
        "experian",
        "Experian",
        Link(
            "Experian: statutory credit report",
            "https://www.experian.co.uk/consumer/statutory-report.html",
            "The service's own site",
        ),
    ),
    Agency(
        "equifax",
        "Equifax",
        Link(
            "Equifax: statutory credit report",
            "https://www.equifax.co.uk/products/credit/statutory-report",
            "The service's own site",
        ),
    ),
    # TransUnion's site turns away automated checks, so this address was confirmed through
    # search results for transunion.co.uk only. Check it by hand before each release.
    Agency(
        "transunion",
        "TransUnion",
        Link(
            "TransUnion: statutory credit report",
            "https://www.transunion.co.uk/consumer/statutory-credit-report",
            "Search results for TransUnion's UK site",
        ),
    ),
)
BY_ID: dict[str, Agency] = {agency.id: agency for agency in AGENCIES}

ICO_ADVICE = Link(
    "ICO: credit reports and fixing mistakes",
    "https://ico.org.uk/for-the-public/credit/",
    "The service's own site",
)


@dataclass(frozen=True)
class Status:
    agency: Agency
    last_checked_on: date | None
    reminder: alerts.Reminder | None
    note: str

    @property
    def anchor(self) -> str:
        return f"credit-{self.agency.id}"


def topic(agency_id: str) -> str:
    return TOPIC + agency_id


def _agency(agency_id: str) -> Agency:
    try:
        return BY_ID[agency_id]
    except KeyError:
        raise ProfileError("Stealth doesn't know that credit reference agency.") from None


def _every(days: int) -> int:
    if days not in {d for d, _label in EVERY_CHOICES}:
        raise ProfileError("Choose how often to be reminded from the list.")
    return days


def statuses(profile: Profile) -> list[Status]:
    rows = {r["agency"]: r for r in profile.conn.execute("SELECT * FROM credit_check")}
    due = {r.topic: r for r in alerts.reminders(profile, TOPIC)}
    found = []
    for agency in AGENCIES:
        row = rows.get(agency.id)
        checked = row["last_checked_on"] if row is not None else None
        found.append(
            Status(
                agency=agency,
                last_checked_on=date.fromisoformat(checked) if checked else None,
                reminder=due.get(topic(agency.id)),
                note=row["note"] if row is not None else "",
            )
        )
    return found


def _remind(profile: Profile, agency: Agency, due_on: date, every_days: int) -> alerts.Reminder:
    return alerts.remind(
        profile,
        topic(agency.id),
        title=f"Check your {agency.name} credit report",
        message=(
            f"Get your free statutory report from {agency.name}, look for anything you don't "
            "recognise, then mark it checked in Stealth."
        ),
        link=f"/protect#credit-{agency.id}",
        due_on=due_on,
        every_days=every_days,
    )


def mark_checked(profile: Profile, agency_id: str, today: date, every_days: int) -> Status:
    """The person looked at this agency's report today: record it and remind them next time."""
    agency = _agency(agency_id)
    days = _every(every_days)
    profile.conn.execute(
        """INSERT INTO credit_check (agency, last_checked_on) VALUES (?, ?)
           ON CONFLICT (agency) DO UPDATE SET last_checked_on = excluded.last_checked_on""",
        (agency.id, today.isoformat()),
    )
    alerts.dismiss_topic(profile, topic(agency.id))
    _remind(profile, agency, today + timedelta(days=days), days)
    return _status(profile, agency.id)


def set_every(profile: Profile, agency_id: str, every_days: int, today: date) -> Status:
    """Change how often to be reminded. Counts from the last check, or from today if none."""
    agency = _agency(agency_id)
    days = _every(every_days)
    last = _status(profile, agency.id).last_checked_on
    due_on = (last or today) + timedelta(days=days)
    _remind(profile, agency, max(due_on, today), days)
    return _status(profile, agency.id)


def stop_reminders(profile: Profile, agency_id: str) -> None:
    agency = _agency(agency_id)
    alerts.forget_reminder(profile, topic(agency.id))
    alerts.dismiss_topic(profile, topic(agency.id))


def save_note(profile: Profile, agency_id: str, note: str) -> None:
    """What looked wrong, in the person's own words. Kept only in their encrypted profile."""
    agency = _agency(agency_id)
    text = note.strip()
    if len(text) > NOTE_LIMIT:
        raise ProfileError(f"Keep the note under {NOTE_LIMIT} characters.")
    profile.conn.execute(
        """INSERT INTO credit_check (agency, note, note_updated_at) VALUES (?, ?, ?)
           ON CONFLICT (agency) DO UPDATE SET note = excluded.note,
               note_updated_at = excluded.note_updated_at""",
        (agency.id, text, clock.now().isoformat()),
    )


def _status(profile: Profile, agency_id: str) -> Status:
    return next(s for s in statuses(profile) if s.agency.id == agency_id)
