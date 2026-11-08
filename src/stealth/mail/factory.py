"""Connecting a profile to its mailbox."""

from __future__ import annotations

from stealth.core.errors import ProfileError
from stealth.mail import google_oauth
from stealth.mail.base import Mailbox, Transport
from stealth.mail.gmail_api import GmailApiMailbox, GmailApiTransport, GmailSession
from stealth.mail.smtp_imap import ImapMailbox, SmtpTransport
from stealth.profiles.models import GOOGLE_PROVIDER
from stealth.profiles.store import Profile


def open_mail(profile: Profile) -> tuple[Transport, Mailbox]:
    account = profile.mail_account()
    if account is None:
        raise ProfileError("No email account connected yet.", hint="Run `stealth init`.")
    # For an app-password account this is the password; after Google sign-in it's the refresh
    # token. Either way it lives only in the keychain, and deleting the profile removes it.
    secret = profile.secrets.get(profile.mail_secret_name)
    if account.provider == GOOGLE_PROVIDER:
        client = google_oauth.client()
        if secret is None or client is None:
            raise ProfileError(
                "Stealth isn't signed in to Google on this computer.",
                hint="Sign in with Google again in Settings, or run `stealth google`.",
            )
        session = GmailSession(client, secret)
        return GmailApiTransport(session), GmailApiMailbox(session)
    if secret is None:
        raise ProfileError(
            "Your email password isn't saved on this computer.",
            hint="Run `stealth password` to enter it again.",
        )
    return SmtpTransport(account, secret), ImapMailbox(account, secret)
