"""Calendar arithmetic for legal deadlines. Pure functions: no clock, no database."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from stealth.core.jurisdictions import Jurisdiction
from stealth.profiles.models import Settings


def add_months(start: date, months: int) -> date:
    """The same day `months` later, clamped to the month's end (31 Jan + 1 month = 28/29 Feb)."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day))


def _after(start: date, months: int, days: int) -> date:
    return add_months(start, months) + timedelta(days=days)


def response_deadline(relevant_day: date, jurisdiction: Jurisdiction) -> date:
    """When a reply is due.

    `relevant_day` is the UK GDPR Art 12A "relevant time": the latest of the request arriving, or
    the company receiving the ID or fee it reasonably asked for. So it moves; see the case engine.
    Elsewhere it's simply when the request (or the information asked for) arrived.
    """
    return _after(relevant_day, jurisdiction.deadline_months, jurisdiction.deadline_days)


def extended_deadline(relevant_day: date, jurisdiction: Jurisdiction) -> date:
    """The deadline after a company validly takes its extension. Where the law allows none
    (Switzerland, Canada, Australia), a company saying it needs longer doesn't move the date."""
    return _after(
        relevant_day,
        jurisdiction.deadline_months + jurisdiction.extension_months,
        jurisdiction.deadline_days + jurisdiction.extension_days,
    )


def ico_ready_on(complaint_day: date, settings: Settings) -> date:
    """When a complaint has had a fair chance. Section 164A allows 30 days just to acknowledge."""
    return complaint_day + timedelta(days=settings.ico_ready_days)


def resend_on(last_request_day: date, category: str, settings: Settings) -> date:
    """When to ask again, because brokers re-list people."""
    days = (
        settings.resend_days_people_search
        if category == "people-search"
        else settings.resend_days_other
    )
    return last_request_day + timedelta(days=days)
