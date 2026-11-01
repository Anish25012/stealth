"""Plain data types for a profile."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "resend_days_people_search": 60,
    "resend_days_other": 90,
    "send_delay_seconds": 20,
    # A sustained rate an ordinary mailbox can carry. Google's ceiling is about 500 a day, and a
    # burst of near-identical legal letters is exactly what its abuse systems look for.
    "daily_send_limit": 40,
    "ico_ready_days": 30,
    "skip_brokers": [],
    "include_extended": False,
    "checklist_done": [],
}


@dataclass
class Settings:
    resend_days_people_search: int
    resend_days_other: int
    send_delay_seconds: int
    daily_send_limit: int
    ico_ready_days: int
    skip_brokers: list[str]
    include_extended: bool
    checklist_done: list[str]


@dataclass
class Person:
    display_name: str
    country: str = "GB"
    names: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    date_of_birth: str | None = None
    dob_opt_in: bool = False  # date of birth is never sent unless this is explicitly set


@dataclass
class MailAccount:
    provider: str
    address: str
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: str | None = None  # "starttls", "ssl" or "none"
    imap_host: str | None = None
    imap_port: int | None = None
    imap_security: str | None = None
    username: str | None = None
    is_own_mailbox: bool = True  # False switches letters to "acting on behalf of" wording


GMAIL_DEFAULTS: dict[str, Any] = {
    "provider": "gmail-password",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "imap_security": "ssl",
}
