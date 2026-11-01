"""Calendar arithmetic for legal deadlines. Pure functions: no clock, no database."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from stealth.core.jurisdictions import Jurisdiction
from stealth.profiles.models import Settings

EXTENSION_MONTHS = 2  # Art 12A(3): a company may take two further months


def add_months(start: date, months: int) -> date:
    """The same day `months` later, clamped to the month's end (31 Jan + 1 month = 28/29 Feb)."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day))


def response_deadline(relevant_day: date, jurisdiction: Jurisdiction) -> date:
    """When a reply is due.

    `relevant_day` is the UK GDPR Art 12A "relevant time": the latest of the request arriving, or
    the company receiving the ID or fee it reasonably asked for. So it moves; see the case engine.
    """
    return add_months(relevant_day, jurisdiction.deadline_months)


def extended_deadline(relevant_day: date, jurisdiction: Jurisdiction) -> date:
    """The deadline after a company validly takes its extension."""
    return add_months(relevant_day, jurisdiction.deadline_months + EXTENSION_MONTHS)


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
