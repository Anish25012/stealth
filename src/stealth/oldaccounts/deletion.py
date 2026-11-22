"""Finding a company's own "delete your account" page on its website.

It runs only when the person asks, for one company at a time, never as part of the Gmail scan.
It reads at most a few pages of the company's own site, follows its robots.txt, and only accepts
an HTTPS link on that same site, so a lookalike or a third party's page can't be offered.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from stealth.core.errors import StealthError
from stealth.custom.email_extract import registrable
from stealth.custom.policy_fetch import USER_AGENT, Fetch, _links, _robots, http_get

MAX_PAGES = 5
DELETE_LINK = re.compile(
    r"\b(?:delete|close|deactivate|remove|cancel)\s+(?:my\s+|your\s+(?:\w+\s+)?)?account\b"
    r"|\baccount\s+(?:deletion|closure|removal)\b"
    r"|(?:delete|close|remove|deactivate)[-_]?(?:my[-_]?|your[-_]?)?account"
    r"|account[-_](?:deletion|closure|delete|close)",
    re.IGNORECASE,
)
HELP_LINK = re.compile(r"\bhelp\b|\bsupport\b|\bfaq\b|\baccount\b|\bsettings\b|\bprivacy\b", re.I)
COMMON_PATHS = ("help", "support", "account")


def find_deletion_page(domain: str, fetch: Fetch = http_get) -> str | None:
    home = f"https://{domain}/"
    own = registrable(domain)

    def ours(url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme == "https" and registrable(parts.hostname or "") == own

    robots = _robots(home, fetch)
    queue = [home, *(urljoin(home, p) for p in COMMON_PATHS)]
    seen: set[str] = set()
    read = 0
    while queue and read < MAX_PAGES:
        url = queue.pop(0)
        if url in seen or not ours(url) or not robots.can_fetch(USER_AGENT, url):
            seen.add(url)
            continue
        seen.add(url)
        try:
            page = fetch(url)
        except StealthError:
            if url == home:
                raise
            continue
        read += 1
        if page.status >= 400 or not ours(page.url):
            continue
        for link, text in _links(page.text, page.url):
            if not ours(link):
                continue
            if DELETE_LINK.search(text) or DELETE_LINK.search(urlsplit(link).path):
                return link.split("#")[0]
            if HELP_LINK.search(text) and link not in seen:
                queue.insert(0, link)  # a help or account page beats guessing at paths
    return None
