from datetime import UTC, date, datetime
from email.message import EmailMessage

import pytest

from stealth.core.errors import MailAuthError, MailConnectionError, MailSendError
from stealth.devmail.servers import LocalImap, LocalSmtp, bounce_of, reply_to
from stealth.mail.smtp_imap import ImapMailbox, SmtpTransport
from stealth.profiles.models import MailAccount

DAY = datetime(2026, 1, 10, tzinfo=UTC)


def account(smtp_port=1, imap_port=1):
    return MailAccount(
        provider="smtp",
        address="me@example.org",
        smtp_host="127.0.0.1",
        smtp_port=smtp_port,
        smtp_security="none",
        imap_host="127.0.0.1",
        imap_port=imap_port,
        imap_security="none",
        username="me@example.org",
    )


def message(to="dpo@acme.example"):
    m = EmailMessage()
    m["From"] = "me@example.org"
    m["To"] = to
    m["Subject"] = "hello"
    m["Date"] = "Sat, 10 Jan 2026 09:00:00 +0000"
    m["Message-ID"] = "<one@example.org>"
    m.set_content("body")
    return m


def test_send_delivers_with_bcc_in_envelope_only():
    with LocalSmtp() as smtp:
        SmtpTransport(account(smtp.port), "pw").send(message(), bcc="me@example.org")
    delivered = smtp.delivered[0]
    assert delivered.recipients == ["dpo@acme.example", "me@example.org"]
    assert delivered.message["Bcc"] is None


def test_correct_password_is_accepted():
    with LocalSmtp(password="right") as smtp:
        SmtpTransport(account(smtp.port), "right").send(message())
    assert len(smtp.delivered) == 1


def test_wrong_smtp_password_is_explained():
    with LocalSmtp(password="right") as smtp, pytest.raises(MailAuthError) as err:
        SmtpTransport(account(smtp.port), "wrong").send(message())
    assert "app password" in err.value.hint


def test_refused_recipient_is_a_send_error():
    with LocalSmtp() as smtp:
        smtp.reject.add("dead@acme.example")
        with pytest.raises(MailSendError, match="refused"):
            SmtpTransport(account(smtp.port), "pw").send(message("dead@acme.example"))


def test_no_server_is_a_connection_error():
    with pytest.raises(MailConnectionError, match="couldn't reach"):
        SmtpTransport(account(smtp_port=1), "pw").send(message())
    with pytest.raises(MailConnectionError, match="couldn't reach"):
        ImapMailbox(account(imap_port=1), "pw").fetch_since(date(2026, 1, 1))


def test_imap_fetches_mail_since_a_date():
    with LocalImap("pw") as imap:
        imap.add(reply_to(message().as_bytes(), "dpo@acme.example", "old", day=DAY))
        recent = datetime(2026, 2, 1, tzinfo=UTC)
        imap.add(reply_to(message().as_bytes(), "dpo@acme.example", "new", day=recent))
        found = ImapMailbox(account(imap_port=imap.port), "pw").fetch_since(date(2026, 1, 20))
    assert len(found) == 1
    assert b"new" in found[0].raw


def test_imap_is_strictly_read_only():
    with LocalImap("pw") as imap:
        imap.add(reply_to(message().as_bytes(), "dpo@acme.example", "x", day=DAY))
        ImapMailbox(account(imap_port=imap.port), "pw").fetch_since(date(2026, 1, 1))
        commands = imap.state.commands
    assert "EXAMINE" in commands  # read-only open
    assert "SELECT" not in commands
    assert not {"STORE", "EXPUNGE", "APPEND", "DELETE", "COPY", "MOVE"} & set(commands)


def test_wrong_imap_password_is_explained():
    with LocalImap("right") as imap, pytest.raises(MailAuthError):
        ImapMailbox(account(imap_port=imap.port), "wrong").fetch_since(date(2026, 1, 1))


def test_bounce_helper_builds_a_real_dsn():
    raw = bounce_of(message().as_bytes(), "dead@acme.example", day=DAY)
    assert b"multipart/report" in raw
    assert b"Status: 5.1.1" in raw
