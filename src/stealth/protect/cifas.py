"""Cifas Protective Registration: record when it started, and remind before it ends.

Protective Registration puts a warning flag against a person's details in Cifas's National Fraud
Database, so organisations that check it do extra checks that it's really them applying. It costs
£30 for two years, is applied for online only, and is meant for people whose identity is at
heightened risk. Cifas doesn't email anyone when a registration is about to end, which is what
Stealth helps with: it records the start date and reminds the person before and when it ends.

Stealth never applies, pays or talks to Cifas. Facts and links were checked on CHECKED_ON against
Cifas's own pages.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from stealth import alerts
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.store import Profile
from stealth.protect.checklist import Link
from stealth.templates.render import format_date

CHECKED_ON = "2026-11-22"
TOPIC = "cifas:"
MONTHS = 24  # "will remain in place for 24 months, unless you request its removal"
WARN_DAYS = 30  # remind this long before it ends

OFFICIAL_HOSTS = frozenset({"www.cifas.org.uk"})
ABOUT = Link(
    "Cifas: Protective Registration",
    "https://www.cifas.org.uk/pr",
    "The service's own site",
)
APPLY = Link(
    "Cifas: apply for Protective Registration",
    "https://www.cifas.org.uk/services/identity-protection/protective-registration/"
    "application-form",
    "Linked from Cifas's Protective Registration page",
)

NONE = "none"
ACTIVE = "active"
ENDING_SOON = "ending_soon"
ENDED = "ended"


@dataclass(frozen=True)
class Registration:
    started_on: date | None
    ends_on: date | None
    state: str  # NONE, ACTIVE, ENDING_SOON or ENDED

    @property
    def recorded(self) -> bool:
        return self.started_on is not None


def add_months(day: date, months: int) -> date:
    """The same day `months` later, or the month's last day if it's shorter (29 Feb → 28 Feb)."""
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def status(profile: Profile, today: date) -> Registration:
    row = profile.conn.execute("SELECT * FROM cifas_registration WHERE id = 1").fetchone()
    if row is None:
        return Registration(None, None, NONE)
    started, ends = date.fromisoformat(row["started_on"]), date.fromisoformat(row["ends_on"])
    if today >= ends:
        state = ENDED
    elif today >= ends - timedelta(days=WARN_DAYS):
        state = ENDING_SOON
    else:
        state = ACTIVE
    return Registration(started, ends, state)


def record(profile: Profile, started_on: date, today: date) -> Registration:
    """The person has registered (or renewed) with Cifas, starting on `started_on`."""
    if started_on > today:
        raise ProfileError("The start date can't be in the future.")
    if started_on < add_months(today, -MONTHS * 2):
        raise ProfileError("That's too long ago to be a current registration.")
    ends_on = add_months(started_on, MONTHS)
    profile.conn.execute(
        """INSERT INTO cifas_registration (id, started_on, ends_on, recorded_at)
           VALUES (1, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET started_on = excluded.started_on,
               ends_on = excluded.ends_on, recorded_at = excluded.recorded_at""",
        (started_on.isoformat(), ends_on.isoformat(), clock.now().isoformat()),
    )
    _clear_reminders(profile)
    ends_text = format_date(ends_on)
    if ends_on - timedelta(days=WARN_DAYS) > today:
        alerts.remind(
            profile,
            TOPIC + "ending",
            title="Your Cifas Protective Registration ends soon",
            message=(
                f"It ends on {ends_text}, and Cifas won't email you. If you still need it, "
                "apply again on Cifas's website."
            ),
            link="/protect#cifas",
            due_on=ends_on - timedelta(days=WARN_DAYS),
        )
    if ends_on > today:
        alerts.remind(
            profile,
            TOPIC + "ended",
            title="Your Cifas Protective Registration has ended",
            message=(
                f"It ended on {ends_text}. If you renewed it, tell Stealth the new start date. "
                "If you don't need it any more, you can stop tracking it."
            ),
            link="/protect#cifas",
            due_on=ends_on,
        )
    return status(profile, today)


def forget(profile: Profile) -> None:
    """Stop tracking: the person removed it, or let it end and doesn't need it."""
    profile.conn.execute("DELETE FROM cifas_registration WHERE id = 1")
    _clear_reminders(profile)


def _clear_reminders(profile: Profile) -> None:
    for reminder in alerts.reminders(profile, TOPIC):
        alerts.forget_reminder(profile, reminder.topic)
    for alert in alerts.open_alerts(profile, TOPIC):
        alerts.dismiss(profile, alert.id)
