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


@dataclass(frozen=True)
class Provider:
    """Settings for a mail provider that allows app passwords, so people don't type servers."""

    key: str
    name: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    imap_host: str
    imap_port: int
    app_password_url: str


# Outlook, Hotmail and Live are deliberately absent: Microsoft no longer accepts app passwords for
# them, so those accounts need Microsoft sign-in.
PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in [
        Provider(
            "gmail",
            "Gmail",
            "smtp.gmail.com",
            587,
            "starttls",
            "imap.gmail.com",
            993,
            "https://myaccount.google.com/apppasswords",
        ),
        Provider(
            "yahoo",
            "Yahoo Mail",
            "smtp.mail.yahoo.com",
            465,
            "ssl",
            "imap.mail.yahoo.com",
            993,
            "https://login.yahoo.com/account/security",
        ),
        Provider(
            "icloud",
            "iCloud Mail",
            "smtp.mail.me.com",
            587,
            "starttls",
            "imap.mail.me.com",
            993,
            "https://account.apple.com/account/manage",
        ),
    ]
}
MICROSOFT_DOMAINS = frozenset(
    {"outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "live.co.uk", "msn.com"}
)


def provider_account(provider: Provider, address: str, *, is_own_mailbox: bool) -> MailAccount:
    return MailAccount(
        provider=f"{provider.key}-password",
        address=address,
        smtp_host=provider.smtp_host,
        smtp_port=provider.smtp_port,
        smtp_security=provider.smtp_security,
        imap_host=provider.imap_host,
        imap_port=provider.imap_port,
        imap_security="ssl",
        username=address,
        is_own_mailbox=is_own_mailbox,
    )
