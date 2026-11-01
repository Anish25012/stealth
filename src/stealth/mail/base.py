"""The mail interfaces the engine depends on, and message building.

The engine only ever sees these protocols, so app-password mail, Gmail and Microsoft Graph are
interchangeable.
"""

from __future__ import annotations

import email.policy
import email.utils
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from typing import Protocol

from stealth.core import clock
from stealth.templates.render import Letter


@dataclass(frozen=True)
class IncomingMail:
    raw: bytes


class Transport(Protocol):
    # Plain SMTP leaves no copy in the Sent folder, so the person is BCCed for their records.
    # API providers save a sent copy themselves, and a BCC would only burn sending quota.
    wants_bcc: bool

    def send(self, message: EmailMessage, bcc: str | None = None) -> None: ...


class Mailbox(Protocol):
    def fetch_since(self, since: date) -> list[IncomingMail]: ...


def compose(letter: Letter, sender: str, to: str) -> EmailMessage:
    """Build a message. BCC is never a header, so recipients can't see it."""
    message = EmailMessage(policy=email.policy.SMTP)
    message["From"] = sender
    message["To"] = to
    message["Subject"] = letter.subject
    message["Date"] = email.utils.format_datetime(clock.now())
    domain = sender.rsplit("@", 1)[-1]
    message["Message-ID"] = email.utils.make_msgid(domain=domain)
    message.set_content(letter.body)
    return message
