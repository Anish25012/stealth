"""Autopilot for companies the person adds: write to them without asking, but only when it's safe.

Only on Autopilot (and from its start date, if one is set). Even then, Stealth writes by itself
only when all four checks pass for the address it found:

1. It was found on the company's own privacy policy or contact page.
2. Its domain is the company's own (or a parent company's that Stealth's list knows).
3. It looks like a privacy contact: privacy@, dpo@, gdpr@, dataprotection@, or named as the data
   protection contact on the page.
4. Neither the company's website nor the address's domain is on a public list of scam and
   phishing sites (see blocklist.py).

Otherwise nothing is sent: the company waits under "Needs you" with the reason. Letters sent this
way carry the person's name and email only, never their address, phone or date of birth, and each
one is recorded in the audit trail as an Autopilot `custom` event and listed under "Sent
automatically".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from stealth import autopilot
from stealth.cases import engine
from stealth.cases.state import Case
from stealth.custom import store as custom
from stealth.custom.blocklist import Blocklist, BlocklistUnavailable
from stealth.custom.email_extract import registrable
from stealth.db import events
from stealth.profiles.store import Profile
from stealth.registry.loader import Broker

ACTOR = autopilot.ACTOR

PRIVACY_PAGE = re.compile(r"privacy|data[-_ ]?protection|gdpr|contact|legal", re.IGNORECASE)
PRIVACY_LOCAL = re.compile(
    r"^(?:privacy|dpo|gdpr|data[._-]?protection|data[._-]?privacy)(?:[._-][a-z0-9]+)*$"
)
DPO_LABEL = re.compile(
    r"data protection (?:officer|contact|team|lead|manager)|\bdpo\b|privacy officer",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Verdict:
    send: bool
    address: str | None
    page: str | None
    checks: list[Check]

    @property
    def reason(self) -> str | None:
        """Why it wasn't sent, in a sentence, or None if it was."""
        failed = next((c for c in self.checks if not c.ok), None)
        return None if failed is None else failed.detail


def parent_groups(brokers: Iterable[Broker]) -> dict[str, frozenset[str]]:
    """Domains Stealth's list knows belong together, such as a brand and its parent company."""
    groups: dict[str, frozenset[str]] = {}
    for broker in brokers:
        group = frozenset(registrable(d) for d in broker.all_domains if d)
        if len(group) > 1:
            for domain in group:
                groups[domain] = groups.get(domain, frozenset()) | group
    return groups


def _page_check(candidate: dict[str, object], domain: str) -> Check:
    label = "Found on their own privacy or contact page"
    page = str(candidate.get("page") or "")
    parts = urlsplit(page)
    if not page or registrable(parts.hostname or "") != registrable(domain):
        return Check(label, False, "The address wasn't found on the company's own website.")
    if not PRIVACY_PAGE.search(parts.path):
        return Check(
            label, False, f"The address was found on {page}, which isn't a privacy or contact page."
        )
    return Check(label, True, f"Found on {page}")


def _domain_check(address: str, domain: str, parents: dict[str, frozenset[str]]) -> Check:
    label = "The address belongs to the company"
    theirs, own = registrable(address.rsplit("@", 1)[1]), registrable(domain)
    if theirs == own:
        return Check(label, True, f"{theirs} is the company's own domain")
    if theirs in parents.get(own, frozenset()):
        return Check(label, True, f"{theirs} belongs to the same company, by Stealth's list")
    return Check(label, False, f"{address} is on {theirs}, not the company's website {own}.")


def _privacy_check(candidate: dict[str, object]) -> Check:
    label = "It's a privacy contact"
    address = str(candidate["address"])
    local = address.split("@", 1)[0]
    if PRIVACY_LOCAL.match(local):
        return Check(label, True, f"{local}@ is a privacy address")
    if DPO_LABEL.search(str(candidate.get("context") or "")):
        return Check(label, True, "The page names it as the data protection contact")
    return Check(
        label,
        False,
        f"{address} doesn't look like a privacy contact (such as privacy@ or dpo@), and the page "
        "doesn't name it as one.",
    )


def _blocklist_check(address: str, domain: str, blocklist: Blocklist) -> Check:
    label = "Not on a list of scam or phishing sites"
    hosts = {domain, address.rsplit("@", 1)[1]}
    try:
        found = blocklist.listed(hosts)
    except BlocklistUnavailable as exc:
        return Check(label, False, exc.message)
    if found:
        return Check(label, False, f"{domain} is on the {found} list of scam and phishing sites.")
    return Check(label, True, "Checked against " + " and ".join(blocklist.names))


def judge(
    company: custom.CustomCompany, *, parents: dict[str, frozenset[str]], blocklist: Blocklist
) -> Verdict:
    """Whether Stealth may write to this company by itself, and why (or why not)."""
    if not company.candidates:
        found = Check("An address was found", False, "No email address was found on their site.")
        return Verdict(False, None, None, [found])
    chosen, checks = None, []
    for candidate in company.candidates:  # likeliest first: use the first that passes
        checks = [
            _page_check(candidate, company.domain),
            _domain_check(str(candidate["address"]), company.domain, parents),
            _privacy_check(candidate),
        ]
        if all(c.ok for c in checks):
            chosen = candidate
            break
    if chosen is None:  # explain using the likeliest address
        chosen = company.candidates[0]
        checks = [
            _page_check(chosen, company.domain),
            _domain_check(str(chosen["address"]), company.domain, parents),
            _privacy_check(chosen),
        ]
    address = str(chosen["address"])
    checks.append(_blocklist_check(address, company.domain, blocklist))
    page = str(chosen.get("page") or "") or None
    return Verdict(all(c.ok for c in checks), address, page, checks)


def apply(
    profile: Profile, company: custom.CustomCompany, verdict: Verdict
) -> tuple[custom.CustomCompany, Case | None]:
    """Queue the letter if the verdict allows it, or hold the company for the person.

    A queued letter goes with the next send (straight away from the app). Either way the
    decision is written to the audit trail.
    """
    checks = [asdict(c) for c in verdict.checks]
    if not verdict.send or verdict.address is None:
        held = custom.record_auto(
            profile,
            company.id,
            status="held",
            reason=verdict.reason,
            checks=checks,
            page=verdict.page,
        )
        events.append(
            profile.conn, "auto_send_held", actor=ACTOR, company=company.id, reason=verdict.reason
        )
        return held, None
    custom.choose(profile, company.id, email=verdict.address)
    sent = custom.record_auto(
        profile, company.id, status="sent", reason=None, checks=checks, page=verdict.page
    )
    broker = custom.as_brokers(profile)[company.id]
    case = engine.queue_one(
        profile, broker, actor=ACTOR, reason=f"sent automatically to {verdict.address}"
    )
    autopilot.record(
        profile,
        case,
        "custom",
        f"to {verdict.address}, found on {verdict.page}; every check passed",
        address=verdict.address,
        page=verdict.page,
        checks=checks,
    )
    return sent, case
