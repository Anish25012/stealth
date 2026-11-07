"""Finding the right address in a privacy policy.

Policies list many addresses: sales, press, jobs, a parent company's lawyers. The one that matters
is the privacy or data protection contact, usually named near words like "data protection officer"
or "your rights". Each address found gets a score from its name, its domain and the words around
it, so the likeliest one is offered first, with the sentence it came from as evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}\b"
)
# "privacy [at] acme [dot] com" and "privacy(at)acme(dot)com"
AT_RE = re.compile(r"\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\})\s*", re.IGNORECASE)
DOT_RE = re.compile(r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\})\s*", re.IGNORECASE)
TAG_RE = re.compile(r"<(script|style)\b.*?</\1\s*>|<[^>]+>", re.IGNORECASE | re.DOTALL)
MAILTO_RE = re.compile(r"mailto:([^\"'?>\s]+)", re.IGNORECASE)

PRIVACY_NAMES = (
    "dpo",
    "privacy",
    "dataprotection",
    "data.protection",
    "data-protection",
    "dataprivacy",
    "data.privacy",
    "gdpr",
    "dsar",
    "dsr",
    "rights",
    "compliance",
    "legal",
)
UNLIKELY_NAMES = (
    "noreply",
    "no-reply",
    "donotreply",
    "sales",
    "marketing",
    "press",
    "media",
    "jobs",
    "careers",
    "recruitment",
    "hr",
    "invoice",
    "billing",
    "abuse",
    "webmaster",
)
CONTEXT_WORDS = re.compile(
    r"data protection officer|\bdpo\b|privacy|personal (data|information)|gdpr|erasure|"
    r"delete|your rights|subject access|data subject|opt[- ]out",
    re.IGNORECASE,
)
NOT_EMAIL_ENDINGS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")


@dataclass(frozen=True)
class Candidate:
    address: str
    score: int
    context: str  # the words around it on the page
    same_site: bool

    @property
    def reasons(self) -> list[str]:
        local = self.address.split("@", 1)[0]
        found = []
        if any(name in local for name in PRIVACY_NAMES):
            found.append("a privacy address")
        if CONTEXT_WORDS.search(self.context):
            found.append("named near privacy wording")
        if not self.same_site:
            found.append("on a different website")
        return found


def page_text(html: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", unescape(TAG_RE.sub(" ", html)))


def _deobfuscate(text: str) -> str:
    return DOT_RE.sub(".", AT_RE.sub("@", text))


def registrable(domain: str) -> str:
    """Near enough for matching: "privacy.acme.co.uk" and "acme.co.uk" give "acme.co.uk"."""
    labels = domain.lower().strip(".").split(".")
    second_level = {"co", "org", "ac", "gov", "me", "ltd", "plc", "net", "com"}
    if len(labels) >= 3 and labels[-2] in second_level and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def find_addresses(html: str, site_domain: str) -> list[Candidate]:
    """Every plausible contact on the page, likeliest first."""
    text = _deobfuscate(page_text(html))
    found: dict[str, Candidate] = {}
    hits = [(m.group(), m.start()) for m in EMAIL_RE.finditer(text)]
    hits += [(unescape(m.group(1)), -1) for m in MAILTO_RE.finditer(html)]
    site = registrable(site_domain)
    for raw, at in hits:
        address = raw.strip(".").lower()
        if address.endswith(NOT_EMAIL_ENDINGS) or "@" not in address:
            continue
        local, domain = address.split("@", 1)
        context = _around(text, at if at >= 0 else text.lower().find(address))
        same_site = registrable(domain) == site
        score = 0
        if any(name in local for name in PRIVACY_NAMES):
            score += 3
        if any(local.startswith(name) for name in UNLIKELY_NAMES):
            score -= 4
        if CONTEXT_WORDS.search(context):
            score += 2
        score += 2 if same_site else -1
        candidate = Candidate(address, score, context, same_site)
        if address not in found or found[address].score < score:
            found[address] = candidate
    return sorted(found.values(), key=lambda c: (-c.score, c.address))


def _around(text: str, at: int, width: int = 110) -> str:
    if at < 0:
        return ""
    start, end = max(0, at - width), min(len(text), at + width)
    return re.sub(r"\s+", " ", text[start:end]).strip()
