"""Telling a sign-up email from everything else, using only its sender, subject and date.

A sign-up email is the one a company sends when an account is made: "Welcome to ...", "Verify
your email", "Confirm your account", "Thanks for signing up". Receipts, newsletters, password
resets and sign-in alerts look similar but aren't counted, and neither is mail from email
providers, mailing services or the person's own domains, which say nothing about a company.

The rules are deliberately simple and readable, and tests/test_old_accounts.py measures them
against a fixture inbox of realistic messages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from email.header import decode_header, make_header
from email.utils import parseaddr

from stealth.custom.email_extract import registrable


@dataclass(frozen=True)
class Header:
    """What the scan reads of one message. Nothing else is ever fetched."""

    sender: str  # the raw From header
    subject: str
    received_on: date


@dataclass(frozen=True)
class SignUp:
    domain: str  # the company's main domain, such as "spotify.com"
    name: str
    received_on: date


SIGNUP = re.compile(
    r"\bwelcome\b"
    r"|\bverify (?:your |my )?(?:[\w.-]+ )?(?:email|e-mail|address|account)"
    r"|\bconfirm (?:your |my )?(?:[\w.-]+ )?(?:email|e-mail|address|account|registration"
    r"|sign[- ]?up)"
    r"|\bactivate (?:your |my )?(?:new )?(?:[\w.-]+ )?account"
    r"|\bthanks? (?:you )?for (?:signing up|registering|joining|creating|your registration)"
    r"|\b(?:account|profile) (?:has been |was |is )?(?:created|set up|registered)"
    r"|\byour new [\w-]+ account"
    r"|\bcomplete your (?:registration|sign[- ]?up|account)"
    r"|\bfinish (?:setting up|creating) your account"
    r"|\bregistration (?:confirmation|successful|complete)",
    re.IGNORECASE,
)
NOT_SIGNUP = re.compile(
    r"\breceipt\b|\border\b|\binvoice\b|\bpayment\b|\bshipped\b|\bdispatched\b|\bdelivery\b"
    r"|\bpassword\b|\bsign[- ]?in\b|\blog[- ]?in\b|\bsecurity alert\b|\bnew device\b"
    r"|\bnewsletter\b|\bdigest\b|\bwebinar\b|\bsubscription\b|\bsubscribe\b|\bunsubscribe\b"
    r"|\bwelcome back\b|\bwelcome to (?:the )?(?:weekend|week|summer|spring|autumn|winter)\b"
    r"|\b(?:sale|offer|discount|% off)\b|\bcode\b|\bverification code\b"
    r"|\bstatement\b",
    re.IGNORECASE,
)

# Email providers, and services companies send through. Their domain says nothing about which
# company the account is with.
NOT_A_COMPANY = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "google.com",
        "outlook.com",
        "hotmail.com",
        "hotmail.co.uk",
        "live.com",
        "live.co.uk",
        "msn.com",
        "yahoo.com",
        "yahoo.co.uk",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "gmx.co.uk",
        "btinternet.com",
        "sky.com",
        "talktalk.net",
        "virginmedia.com",
        "sendgrid.net",
        "mailchimp.com",
        "mcsv.net",
        "mcdlv.net",
        "amazonses.com",
        "mailgun.org",
        "sparkpostmail.com",
        "mandrillapp.com",
        "exacttarget.com",
        "rsgsv.net",
        "list-manage.com",
        "hubspotemail.net",
        "klaviyomail.com",
    }
)

NAME_NOISE = re.compile(
    r"^(?:the\s+)?|\s+(?:team|support|accounts?|no[- ]?reply|noreply|notifications?|"
    r"customer (?:service|care)|hello|info|mail)$",
    re.IGNORECASE,
)


def _decoded(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError):
        return value


def _name(display: str, domain: str) -> str:
    name = _decoded(display).strip().strip('"').strip()
    for _ in range(2):
        name = NAME_NOISE.sub("", name).strip(" -,|")
    if not name or "@" in name or len(name) > 60:
        label = domain.split(".")[0]
        return label[:1].upper() + label[1:]
    return name


def classify(header: Header, own_domains: frozenset[str] = frozenset()) -> SignUp | None:
    """The company a sign-up email came from, or None if it isn't one."""
    subject = " ".join(_decoded(header.subject).split())
    if not SIGNUP.search(subject) or NOT_SIGNUP.search(subject):
        return None
    display, address = parseaddr(_decoded(header.sender))
    host = address.rpartition("@")[2].strip().lower().rstrip(".")
    if not host or "." not in host:
        return None
    domain = registrable(host)
    if domain in NOT_A_COMPANY or domain in own_domains:
        return None
    return SignUp(domain=domain, name=_name(display, domain), received_on=header.received_on)
