"""Mail servers that run on this computer and talk to nobody else.

They power `stealth demo` and the test suite, so both exercise the real SMTP and IMAP code paths.
They bind to 127.0.0.1 only.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import socket
import socketserver
import threading
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Any, Self

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

LOCALHOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOCALHOST, 0))
        port: int = probe.getsockname()[1]
    return port


@dataclass
class Delivered:
    sender: str
    recipients: list[str]
    data: bytes

    @property
    def message(self) -> email.message.EmailMessage:
        return email.message_from_bytes(self.data, policy=email.policy.default)


class _Handler:
    def __init__(self, owner: LocalSmtp) -> None:
        self.owner = owner

    async def handle_RCPT(
        self, server: Any, session: Any, envelope: Any, address: str, options: list[str]
    ) -> str:
        if address in self.owner.reject:
            return "550 No such user"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:
        self.owner.delivered.append(
            Delivered(envelope.mail_from, list(envelope.rcpt_tos), bytes(envelope.content))
        )
        return "250 Message accepted"


class LocalSmtp:
    """Accepts mail into a list. With `password` set, it demands that password."""

    def __init__(self, password: str | None = None) -> None:
        self.port = _free_port()
        self.delivered: list[Delivered] = []
        self.reject: set[str] = set()

        def check(server: Any, session: Any, envelope: Any, mechanism: str, data: Any) -> Any:
            ok = isinstance(data, LoginPassword) and data.password.decode() == password
            # handled=False makes aiosmtpd send the 535 itself; otherwise the client just hangs.
            return AuthResult(success=ok, handled=False)

        options: dict[str, Any] = {}
        if password is not None:
            options = {"authenticator": check, "auth_required": True, "auth_require_tls": False}
        # server_hostname stops aiosmtpd calling socket.getfqdn(), which can hang on macOS.
        self._controller = Controller(
            _Handler(self),
            hostname=LOCALHOST,
            port=self.port,
            server_hostname="localhost",
            ready_timeout=30,
            **options,
        )

    def __enter__(self) -> Self:
        self._controller.start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._controller.stop()


@dataclass
class _Mail:
    raw: bytes
    day: datetime


@dataclass
class ImapState:
    password: str
    mail: list[_Mail] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)


class _ImapSession(socketserver.StreamRequestHandler):
    """The handful of IMAP4rev1 commands Python's imaplib uses to read a folder."""

    server: _ImapServer

    def _line(self, text: str) -> None:
        self.wfile.write(text.encode() + b"\r\n")

    def handle(self) -> None:
        state = self.server.state
        self._line("* OK local test server ready")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            tag, _, rest = raw.decode().strip().partition(" ")
            verb, _, args = rest.partition(" ")
            verb = verb.upper()
            state.commands.append(verb)
            if verb == "CAPABILITY":
                self._line("* CAPABILITY IMAP4rev1")
                self._line(f"{tag} OK done")
            elif verb == "LOGIN":
                good = args.split(" ", 1)[-1].strip('"') == state.password
                self._line(f"{tag} OK logged in" if good else f"{tag} NO [AUTH] LOGIN failed")
            elif verb in ("SELECT", "EXAMINE"):
                self._line(f"* {len(state.mail)} EXISTS")
                self._line(f"{tag} OK [READ-ONLY] selected")
            elif verb == "SEARCH":
                self._search(tag, args)
            elif verb == "FETCH":
                self._fetch(tag, args)
            elif verb == "LOGOUT":
                self._line("* BYE")
                self._line(f"{tag} OK bye")
                return
            else:
                self._line(f"{tag} BAD unsupported")

    def _search(self, tag: str, args: str) -> None:
        since = datetime.strptime(args.split()[-1], "%d-%b-%Y").date()  # noqa: DTZ007
        state = self.server.state
        hits = [str(i + 1) for i, m in enumerate(state.mail) if m.day.date() >= since]
        self._line(f"* SEARCH {' '.join(hits)}")
        self._line(f"{tag} OK done")

    def _fetch(self, tag: str, args: str) -> None:
        number = int(args.split()[0])
        raw = self.server.state.mail[number - 1].raw
        self.wfile.write(f"* {number} FETCH (BODY[] {{{len(raw)}}}\r\n".encode())
        self.wfile.write(raw)
        self._line(")")
        self._line(f"{tag} OK done")


class _ImapServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    state: ImapState


class LocalImap:
    """A mailbox with one folder. `add` puts mail in it, as a broker's reply would arrive."""

    def __init__(self, password: str) -> None:
        self.port = _free_port()
        self.state = ImapState(password=password)
        self._server = _ImapServer((LOCALHOST, self.port), _ImapSession)
        self._server.state = self.state

    def add(self, raw: bytes) -> None:
        parsed = email.message_from_bytes(raw)
        day = email.utils.parsedate_to_datetime(str(parsed["Date"]))
        self.state.mail.append(_Mail(raw, day))

    def __enter__(self) -> Self:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()


def reply_to(original: bytes, sender: str, body: str, *, day: datetime) -> bytes:
    """A reply to `original`, threaded the way a real mail client would thread it."""
    source = email.message_from_bytes(original, policy=email.policy.default)
    message = email.message.EmailMessage(policy=email.policy.SMTP)
    message["From"] = sender
    message["To"] = str(source["From"])
    message["Subject"] = f"Re: {source['Subject']}"
    message["Date"] = email.utils.format_datetime(day)
    message["Message-ID"] = email.utils.make_msgid(domain=sender.rsplit("@", 1)[-1])
    message["In-Reply-To"] = str(source["Message-ID"])
    message.set_content(body)
    return message.as_bytes()


def bounce_of(original: bytes, recipient: str, *, day: datetime) -> bytes:
    """A permanent-failure delivery report, byte for byte the shape a mail server sends."""
    source = email.message_from_bytes(original, policy=email.policy.default)
    boundary = "stealth-dsn-boundary"
    lines = [
        "From: Mail Delivery Subsystem <mailer-daemon@mail.example.test>",
        f"To: {source['From']}",
        "Subject: Delivery Status Notification (Failure)",
        f"Date: {email.utils.format_datetime(day)}",
        f"Message-ID: {email.utils.make_msgid(domain='mail.example.test')}",
        "MIME-Version: 1.0",
        f'Content-Type: multipart/report; report-type=delivery-status; boundary="{boundary}"',
        "",
        f"--{boundary}",
        "Content-Type: text/plain",
        "",
        f"Delivery to {recipient} failed permanently.",
        f"--{boundary}",
        "Content-Type: message/delivery-status",
        "",
        "Reporting-MTA: dns; mail.example.test",
        "",
        f"Final-Recipient: rfc822; {recipient}",
        "Action: failed",
        "Status: 5.1.1",
        "",
        f"--{boundary}--",
        "",
    ]
    return "\r\n".join(lines).encode()
