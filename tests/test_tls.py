"""Every TLS connection verifies against the operating system's trust store.

HTTPS-inspecting antivirus (Norton, Avast) adds its own root there; a bundled certifi list
doesn't have it, which broke update checks on machines running it.
"""

import ssl
import urllib.request

import pytest

from stealth.core import tls
from stealth.custom import policy_fetch
from stealth.registry import updater


def test_the_context_is_the_operating_systems_and_still_strict():
    ctx = tls.context()
    assert type(ctx).__module__.startswith("truststore")
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


@pytest.mark.parametrize(
    "fetch",
    [
        lambda: updater.fetch_bytes("https://example.test/x"),
        lambda: policy_fetch.http_get("https://example.test/x"),
    ],
)
def test_downloads_use_it(monkeypatch, fetch):
    seen = []

    class Answer:
        status = 200
        headers = type("H", (), {"get_content_charset": staticmethod(lambda: "utf-8")})()

        def read(self, n):
            return b"ok"

        def geturl(self):
            return "https://example.test/x"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout, context=None):
        seen.append(context)
        return Answer()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    fetch()
    assert seen and type(seen[0]).__module__.startswith("truststore")


def test_mail_uses_it(monkeypatch):
    import smtplib

    from stealth.mail.smtp_imap import SmtpTransport
    from stealth.profiles.models import MailAccount

    contexts = []
    monkeypatch.setattr(
        smtplib, "SMTP_SSL", lambda *a, context=None, **k: contexts.append(context) or object()
    )
    account = MailAccount("smtp", "a@b.test", "smtp.b.test", 465, "ssl")
    SmtpTransport(account, "pw")._connect()
    assert type(contexts[0]).__module__.startswith("truststore")


# Naming the antivirus when it blocks a mail connection --------------------------------------


def test_a_certificate_signed_by_norton_names_norton_and_the_setting():
    import ssl

    from stealth.mail import smtp_imap

    def norton(host, port):
        return "Norton Web/Mail Shield"

    error = smtp_imap._network_error(
        "imap.gmail.com", ssl.SSLCertVerificationError("untrusted root"), 993, issuer=norton
    )
    assert error.message == "Norton is blocking Stealth's connection to imap.gmail.com."
    assert "Settings, Antivirus, Email Protection" in error.hint


def test_an_unknown_issuer_keeps_the_general_message():
    import ssl

    from stealth.mail import smtp_imap

    error = smtp_imap._network_error(
        "imap.gmail.com", ssl.SSLCertVerificationError("x"), 993, issuer=lambda h, p: None
    )
    assert error.message.startswith("Stealth couldn't confirm it was really talking to")
    assert tls.antivirus_named("Avast Web/Mail Shield Root") == "Avast"
    assert tls.antivirus_named("Google Trust Services") is None
