"""Connecting a profile to its mailbox."""

from __future__ import annotations

from stealth.core.errors import ProfileError
from stealth.mail.base import Mailbox, Transport
from stealth.mail.smtp_imap import ImapMailbox, SmtpTransport
from stealth.profiles.store import Profile


def open_mail(profile: Profile) -> tuple[Transport, Mailbox]:
    account = profile.mail_account()
    if account is None:
        raise ProfileError("No email account connected yet.", hint="Run `stealth init`.")
    password = profile.secrets.get(profile.mail_secret_name)
    if password is None:
        raise ProfileError(
            "Your email password isn't saved on this computer.",
            hint="Run `stealth init` to enter it again.",
        )
    return SmtpTransport(account, password), ImapMailbox(account, password)
