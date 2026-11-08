"""Looking up a company that isn't on Stealth's list: find its privacy policy, then its address.

Given a website, Stealth reads the home page, follows links that look like a privacy policy, and
tries the usual addresses (/privacy, /privacy-policy). It reads a handful of pages at most, only on
that company's own site, only over HTTPS, and not at all where the site's robots.txt says no. The
person always sees what was found and where, and picks the address themselves.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from stealth import __version__
from stealth.core import tls
from stealth.core.errors import StealthError
from stealth.custom.email_extract import Candidate, find_addresses, registrable

USER_AGENT = (
    f"Stealth/{__version__} (privacy request helper; +https://github.com/Anish25012/stealth)"
)
MAX_PAGES = 6
MAX_BYTES = 2_000_000
TIMEOUT = 10
COMMON_PATHS = ("privacy", "privacy-policy", "privacy-notice", "legal/privacy", "privacy.html")
POLICY_LINK = re.compile(r"privacy|data[ -]?protection|gdpr|your[ -]data", re.IGNORECASE)
FORM_LINK = re.compile(
    r"data[ -]?(subject|rights)?[ -]?request|subject[ -]access|dsar|privacy[ -]?(request|portal|"
    r"centre|center)|opt[ -]?out|do[ -]not[ -]sell|remove[ -]my|erasure",
    re.IGNORECASE,
)
LINK_RE = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
SITE_NAME_RE = re.compile(
    r"<meta[^>]+property=[\"']og:site_name[\"'][^>]+content=[\"']([^\"']+)", re.IGNORECASE
)
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


@dataclass(frozen=True)
class Page:
    url: str  # after redirects
    status: int
    text: str


Fetch = Callable[[str], Page]


@dataclass
class Lookup:
    domain: str
    name: str
    candidates: list[Candidate] = field(default_factory=list)
    policy_url: str | None = None
    forms: list[str] = field(default_factory=list)
    pages_checked: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)  # pages robots.txt asked us not to read

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


def normalise_domain(site: str) -> str:
    """ "https://www.Acme.co.uk/about" or "acme.co.uk" -> "acme.co.uk"."""
    text = site.strip().lower()
    if "@" in text and "/" not in text:
        text = text.rsplit("@", 1)[1]  # someone pasted an email address
    if "://" not in text:
        text = f"https://{text}"
    host = (urlsplit(text).hostname or "").strip(".")
    host = host.removeprefix("www.")
    if not DOMAIN_RE.match(host) or "." not in host:
        raise StealthError(
            "That doesn't look like a website address.",
            hint="Type the company's website, for example acme.co.uk.",
        )
    return host


def http_get(url: str) -> Page:
    """One polite HTTPS request: a clear user agent, a timeout and a size limit."""
    if not url.startswith("https://"):
        raise StealthError(f"Stealth only reads websites over HTTPS: {url}")
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - https only
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=tls.context()) as response:  # noqa: S310 - https only
            body = response.read(MAX_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
            return Page(response.geturl(), response.status, body.decode(charset, errors="replace"))
    except urllib.error.HTTPError as exc:
        return Page(url, exc.code, "")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise StealthError(
            f"Couldn't reach {urlsplit(url).hostname}.",
            hint=f"Check the address and your internet connection. ({reason})",
        ) from exc


def _robots(base: str, fetch: Fetch) -> RobotFileParser:
    parser = RobotFileParser()
    try:
        page = fetch(urljoin(base, "/robots.txt"))
    except StealthError:
        page = Page("", 404, "")
    # No robots.txt (or an unreadable one) means no rules.
    parser.parse([] if page.status >= 400 else page.text.splitlines())
    return parser


def _site_name(html: str, domain: str) -> str:
    found = SITE_NAME_RE.search(html)
    if found:
        return unescape(found.group(1)).strip()[:60]
    title = TITLE_RE.search(html)
    if title:
        parts = [p.strip() for p in re.split(r"\s[|\-\u2013\u2014:]\s", unescape(title.group(1)))]
        parts = [p for p in parts if 1 < len(p) <= 40]
        if parts:
            return min(parts, key=len) if len(parts) > 1 else parts[0]
    return domain


def _links(html: str, page_url: str) -> list[tuple[str, str]]:
    return [
        (urljoin(page_url, unescape(href.strip())), re.sub(r"<[^>]+>|\s+", " ", text).strip())
        for href, text in LINK_RE.findall(html)
    ]


def look_up(site: str, fetch: Fetch = http_get) -> Lookup:
    domain = normalise_domain(site)
    home = f"https://{domain}/"
    robots = _robots(home, fetch)
    lookup = Lookup(domain=domain, name=domain)
    own_site = registrable(domain)

    def ours(url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme == "https" and registrable(parts.hostname or "") == own_site

    queue = [home, *(urljoin(home, path) for path in COMMON_PATHS)]
    seen: set[str] = set()
    best_score = -99
    while queue and len(lookup.pages_checked) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen or not ours(url):
            continue
        seen.add(url)
        if not robots.can_fetch(USER_AGENT, url):
            lookup.blocked.append(url)
            continue
        try:
            page = fetch(url)
        except StealthError:
            if url == home:
                raise
            continue
        if page.status >= 400 or not ours(page.url):
            continue
        lookup.pages_checked.append(page.url)
        if url == home:
            lookup.name = _site_name(page.text, domain)
        for candidate in find_addresses(page.text, domain):
            known = next((c for c in lookup.candidates if c.address == candidate.address), None)
            if known is None or known.score < candidate.score:
                lookup.candidates = [c for c in lookup.candidates if c is not known]
                lookup.candidates.append(candidate)
            if candidate.score > best_score:
                best_score, lookup.policy_url = candidate.score, page.url
        for link, text in _links(page.text, page.url):
            if FORM_LINK.search(text) or FORM_LINK.search(link):
                if link.startswith("https://") and link not in lookup.forms:
                    lookup.forms.append(link)
            elif (POLICY_LINK.search(text) or POLICY_LINK.search(link)) and ours(link):
                queue.insert(0, link)  # a policy link beats guessing at paths
    lookup.candidates.sort(key=lambda c: (-c.score, c.address))
    lookup.candidates = lookup.candidates[:6]
    lookup.forms = lookup.forms[:4]
    return lookup
