"""Following a company's "click to confirm your request" link, on Autopilot, only when it's safe.

A link is followed only if all of these hold:

- the email belongs to that company's case: it answers our letter (threaded, or quotes the case
  reference), or it comes from their domain *and* the provider vouches for that (DMARC, or DKIM or
  SPF passing for their domain, in Authentication-Results), since anyone can fake a From line;
- the email didn't fail its sender checks (SPF, DKIM or DMARC "fail");
- there is exactly one confirmation link in it, over HTTPS, on the company's own domain, with no
  user name, password or unusual port in it;
- every redirect stays on HTTPS and on the company's own domain;
- it's one plain page visit (GET). Nothing is filled in, submitted or clicked on the page.

The visit counts as done only when the page says so ("confirmed", "verified", "thank you") and
shows no CAPTCHA. Otherwise the link goes to the person, with the reason.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from email.message import Message
from html import unescape
from urllib.parse import urlsplit

from stealth.core import tls

TIMEOUT = 20
MAX_BYTES = 512 * 1024
USER_AGENT = "Stealth (data-removal requests; one confirmation visit)"

# What a confirmation link looks like, as opposed to the privacy policy, the logo or unsubscribe.
CONFIRM_URL = re.compile(
    r"verif|confirm|validat|activat|token|optout|opt-out|opt_out|suppress|remov|complete",
    re.IGNORECASE,
)
NOT_CONFIRM_URL = re.compile(r"unsubscribe|privacy-policy|privacy_policy|/terms|/help", re.I)
CAPTCHA = re.compile(r"captcha|cf-turnstile|challenge-platform|are you a robot|not a robot", re.I)
DONE = re.compile(
    r"\b(confirmed|verified|validated|thank you|thanks|success(fully)?|has been (received|"
    r"submitted|processed|removed)|will be (removed|processed)|complete[d]?)\b",
    re.IGNORECASE,
)
FAILED_CHECK = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*(fail|softfail|permerror)\b", re.I)
TAGS = re.compile(r"<(script|style)[\s\S]*?</\1>|<[^>]+>", re.IGNORECASE)


@dataclass(frozen=True)
class Page:
    url: str  # where it ended up
    status: int
    text: str


@dataclass(frozen=True)
class Result:
    done: bool
    url: str | None  # the link chosen, if any
    why: str  # for the case history, and Needs you when not done


Get = Callable[[str, set[str]], Page]


def _on(host: str, domains: Iterable[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith(f".{d}") for d in domains)


def safe_url(url: str, domains: set[str]) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and parts.port in (None, 443)
        and _on(parts.hostname or "", domains)
    )


def confirmation_links(urls: Iterable[str]) -> list[str]:
    """Links that look like "confirm your request", not the logo, policy or unsubscribe."""
    links = [
        unescape(u).rstrip(".,;:!")
        for u in urls
        if CONFIRM_URL.search(u) and not NOT_CONFIRM_URL.search(u)
    ]
    return list(dict.fromkeys(links))


def checks_failed(headers: Message | None) -> bool:
    if headers is None:
        return False
    results = " ".join(str(v) for v in headers.get_all("Authentication-Results", []))
    return bool(FAILED_CHECK.search(results))


PASSED = re.compile(r"\b(?P<check>dmarc|dkim|spf)\s*=\s*pass\b(?P<rest>[^;]*)", re.IGNORECASE)
FOR_DOMAIN = re.compile(r"\b(header\.from|header\.d|header\.i|smtp\.mailfrom)=@?([^\s;()]+)", re.I)


def vouched_for(headers: Message | None, domains: set[str]) -> bool:
    """The receiving provider confirmed the email really came from the company's domain."""
    # Only the topmost header: that's the one the person's own provider added on arrival. Anyone
    # sending the email can put more below it.
    results = str(headers.get("Authentication-Results", "")) if headers is not None else ""
    for found in PASSED.finditer(results):
        for _key, value in FOR_DOMAIN.findall(found["rest"]):
            if _on(value.rsplit("@", 1)[-1], domains):
                return True
    return False


def follow(
    urls: Iterable[str],
    domains: set[str],
    headers: Message | None = None,
    get: Get | None = None,
    *,
    proven: bool = False,
) -> Result:
    """Visit the confirmation link if the rules allow, and say whether it's done.

    `proven`: the email answers our own letter. Otherwise its sender must be vouched for.
    """
    if checks_failed(headers):
        return Result(False, None, "the email failed its sender checks, so Stealth didn't open it")
    if not proven and not vouched_for(headers, domains):
        return Result(False, None, "Stealth couldn't confirm the email really came from them")
    links = confirmation_links(urls)
    theirs = [u for u in links if safe_url(u, domains)]
    if len(theirs) > 1:
        return Result(False, None, "there was more than one link, so Stealth didn't guess")
    if not theirs:
        if links:
            return Result(False, links[0], "the link isn't on the company's own website over HTTPS")
        return Result(False, None, "Stealth couldn't find the confirmation link")
    link = theirs[0]
    try:
        page = (get or http_get)(link, domains)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Result(False, link, f"the page didn't open ({getattr(exc, 'reason', exc)})")
    if not safe_url(page.url, domains):
        return Result(False, link, "the link went to another website, so Stealth stopped")
    if not 200 <= page.status < 300:
        return Result(False, link, f"the page didn't open (error {page.status})")
    if CAPTCHA.search(page.text):
        return Result(False, link, "the page asks you to prove you're not a robot")
    words = " ".join(TAGS.sub(" ", page.text).split())
    if not DONE.search(unescape(words)):
        return Result(False, link, "the page didn't say it was confirmed; it may need a click")
    return Result(True, link, f"opened the confirmation link on {urlsplit(link).hostname}")


class _SameSite(urllib.request.HTTPRedirectHandler):
    """Follows redirects only while they stay on HTTPS and the company's own domain."""

    def __init__(self, domains: set[str]) -> None:
        self.domains = domains

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not safe_url(newurl, self.domains):
            raise urllib.error.HTTPError(
                newurl, code, "redirect off the company's site", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str, domains: set[str]) -> Page:
    """One GET, with a timeout, a size limit and redirects kept on the company's site."""
    if not safe_url(url, domains):
        raise ValueError("not a safe link")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=tls.context()), _SameSite(domains)
    )
    request = urllib.request.Request(  # noqa: S310 - https only, checked above
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, method="GET"
    )
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
            return Page(response.geturl(), response.status, body.decode(charset, "replace"))
    except urllib.error.HTTPError as exc:
        if "redirect off" in str(exc.msg):
            return Page(str(exc.filename or ""), exc.code, "")
        return Page(url, exc.code, "")
