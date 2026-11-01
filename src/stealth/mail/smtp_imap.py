"""Mail over SMTP and IMAP, used with an app password.

Each failure is turned into a message a person can act on. IMAP is opened read-only, and messages
are fetched with BODY.PEEK so nothing in the mailbox changes, not even the unread flag.
"""

from __future__ import annotations

import contextlib
import imaplib
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage

from stealth.core.errors import MailAuthError, MailConnectionError, MailSendError
from stealth.mail.base import IncomingMail
from stealth.profiles.models import MailAccount

TIMEOUT_SECONDS = 30
# Sent in EHLO. The default is this computer's own name, which is both personal and, on macOS,
# slow to look up.
EHLO_NAME = "localhost"


def _connection_error(host: str | None) -> MailConnectionError:
    return MailConnectionError(
        f"Stealth couldn't reach your email provider ({host}).",
        hint="Check your internet connection and the server name, then try again.",
    )


def _network_error(host: str | None, exc: Exception) -> MailConnectionError:
    """The most useful message for a failed connection. People act on this text alone."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return MailConnectionError(
            f"Stealth couldn't confirm it was really talking to {host}, so it stopped.",
            hint="Antivirus email or web scanning is the usual cause. Turn off its email scanning, "
            "or use a different network, and try again.",
        )
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return MailConnectionError(
            f"The connection to {host} was closed before Stealth finished.",
            hint="Antivirus email scanning can do this. Check your app password, then try again.",
        )
    return _connection_error(host)


def _auth_error() -> MailAuthError:
    return MailAuthError(
        "Your email provider rejected the sign-in.",
        hint="Gmail needs an app password, not your normal password: "
        "https://myaccount.google.com/apppasswords",
    )


class SmtpTransport:
    wants_bcc = True

    def __init__(self, account: MailAccount, password: str) -> None:
        self.account = account
        self.password = password

    def _connect(self) -> smtplib.SMTP:
        host, port = self.account.smtp_host or "", self.account.smtp_port or 587
        if self.account.smtp_security == "ssl":
            context = ssl.create_default_context()
            return smtplib.SMTP_SSL(host, port, EHLO_NAME, timeout=TIMEOUT_SECONDS, context=context)
        server = smtplib.SMTP(host, port, EHLO_NAME, timeout=TIMEOUT_SECONDS)
        if self.account.smtp_security == "starttls":
            server.starttls(context=ssl.create_default_context())
        return server

    def send(self, message: EmailMessage, bcc: str | None = None) -> None:
        # A fresh connection per message: with long pacing delays, providers drop idle sessions.
        recipients = [str(message["To"])] + ([bcc] if bcc else [])
        try:
            with self._connect() as server:
                # has_extn reads what EHLO announced, so EHLO must come first, and STARTTLS
                # discards it. Without this the login is silently skipped.
                server.ehlo_or_helo_if_needed()
                if self.account.username and server.has_extn("auth"):
                    server.login(self.account.username, self.password)
                refused = server.send_message(message, to_addrs=recipients)
            # With a BCC, smtplib only raises if everyone is refused. A company's address failing
            # while our own copy succeeds comes back here instead, and must not count as sent.
            if str(message["To"]) in refused:
                raise MailSendError(
                    f"Your email provider refused to send to {message['To']}.",
                    hint="This contact may be out of date.",
                )
        except smtplib.SMTPAuthenticationError as exc:
            raise _auth_error() from exc
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            raise MailSendError(
                f"Your email provider refused to send to {message['To']}.",
                hint="This contact may be out of date.",
            ) from exc
        except smtplib.SMTPDataError as exc:
            raise MailSendError(
                "Your email provider refused this message.",
                hint="You may have reached its daily sending limit. Stealth will try again.",
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise _network_error(self.account.smtp_host, exc) from exc


class ImapMailbox:
    def __init__(self, account: MailAccount, password: str, folder: str = "INBOX") -> None:
        self.account = account
        self.password = password
        self.folder = folder

    def _connect(self) -> imaplib.IMAP4:
        host, port = self.account.imap_host or "", self.account.imap_port or 993
        if self.account.imap_security == "ssl":
            context = ssl.create_default_context()
            return imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=TIMEOUT_SECONDS)
        return imaplib.IMAP4(host, port, timeout=TIMEOUT_SECONDS)

    def fetch_since(self, since: date) -> list[IncomingMail]:
        try:
            server = self._connect()
        except OSError as exc:
            raise _network_error(self.account.imap_host, exc) from exc
        try:
            server.login(self.account.username or self.account.address, self.password)
            status, _ = server.select(self.folder, readonly=True)
            if status != "OK":
                raise MailConnectionError(f"Couldn't open the {self.folder} folder.")
            return self._fetch(server, since)
        except imaplib.IMAP4.error as exc:
            if "login" in str(exc).lower() or "auth" in str(exc).lower():
                raise _auth_error() from exc
            raise _connection_error(self.account.imap_host) from exc
        except OSError as exc:
            raise _network_error(self.account.imap_host, exc) from exc
        finally:
            # Already disconnected is fine: the work above has finished or failed by now.
            with contextlib.suppress(imaplib.IMAP4.error, OSError):
                server.logout()

    @staticmethod
    def _fetch(server: imaplib.IMAP4, since: date) -> list[IncomingMail]:
        stamp = f"{since.day}-{since.strftime('%b-%Y')}"
        _, data = server.search(None, "SINCE", stamp)
        found = []
        for number in (data[0] or b"").split():
            _, parts = server.fetch(number.decode(), "(BODY.PEEK[])")
            for part in parts:
                if isinstance(part, tuple):
                    found.append(IncomingMail(raw=part[1]))
        return found
