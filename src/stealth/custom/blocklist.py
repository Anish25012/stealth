"""Public lists of scam and phishing websites, checked on this computer.

Auto-send never writes to a domain on these lists. Stealth downloads each list whole and looks the
domain up locally, so the lists' owners never learn which companies the person deals with. Only
the website names are kept, in Stealth's own folder, for a day. If a list can't be downloaded and
there's no recent copy, the check fails and the letter waits for the person instead.

URLhaus's free downloads have been withdrawn (they now answer "410 Gone"), so the lists used are
PhishTank's verified phishing sites and OpenPhish's community feed, which need no account.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from stealth.core import clock, tls
from stealth.core.errors import StealthError
from stealth.custom.policy_fetch import USER_AGENT

FRESH_FOR = timedelta(hours=24)  # download again after this
USABLE_FOR = timedelta(days=7)  # an older copy is still used if a download fails, up to this
MAX_BYTES = 40_000_000
TIMEOUT = 60

Download = Callable[[str], bytes]


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    kind: str  # "csv" (PhishTank: a "url" column) or "lines" (one URL per line)


FEEDS = (
    Feed("PhishTank", "https://data.phishtank.com/data/online-valid.csv", "csv"),
    Feed("OpenPhish", "https://openphish.com/feed.txt", "lines"),
)


class BlocklistUnavailable(StealthError):
    pass


class Blocklist(Protocol):
    @property
    def names(self) -> tuple[str, ...]: ...

    def listed(self, hosts: Iterable[str]) -> str | None:
        """The name of a list one of these website names is on, or None.

        Raises BlocklistUnavailable if the lists couldn't be checked.
        """
        ...


def host_of(value: str) -> str:
    """A bare website name from a URL or a domain: "https://www.Bad.example/x" -> "bad.example"."""
    text = value.strip().lower()
    if "://" not in text:
        text = "https://" + text
    host = (urlsplit(text).hostname or "").strip(".")
    return host.removeprefix("www.")


def parse(feed: Feed, body: bytes) -> set[str]:
    text = body.decode("utf-8", errors="replace")
    if feed.kind == "csv":
        urls: Iterable[str] = (row.get("url", "") for row in csv.DictReader(io.StringIO(text)))
    else:
        urls = (line for line in text.splitlines() if line.strip() and not line.startswith("#"))
    return {h for h in (host_of(u) for u in urls) if h}


def https_download(url: str) -> bytes:
    """Fetch a whole list over HTTPS. Redirects are followed only to other HTTPS addresses."""
    if not url.startswith("https://"):
        raise BlocklistUnavailable(f"Stealth only downloads lists over HTTPS: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=tls.context()) as response:  # noqa: S310
            if not response.geturl().startswith("https://"):
                raise BlocklistUnavailable("The list moved to an address that isn't HTTPS.")
            body: bytes = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BlocklistUnavailable(f"Couldn't download {urlsplit(url).hostname}.") from exc
    if len(body) > MAX_BYTES:
        raise BlocklistUnavailable(f"The list from {urlsplit(url).hostname} is too big.")
    return body


@dataclass
class FeedBlocklist:
    """The real lists, cached in `folder` (Stealth's own folder, not the person's profile)."""

    folder: Path
    download: Download | None = None  # None: https_download, looked up when used
    feeds: tuple[Feed, ...] = FEEDS
    _hosts: dict[str, set[str]] = field(default_factory=dict)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.feeds)

    def _cache(self, feed: Feed) -> Path:
        return self.folder / f"{feed.name.lower()}.json"

    def _load(self, feed: Feed) -> set[str]:
        if feed.name in self._hosts:
            return self._hosts[feed.name]
        now = clock.now()
        cached: dict[str, Any] | None = None
        path = self._cache(feed)
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
        age = (
            now - datetime.fromisoformat(str(cached["fetched_at"]))
            if cached and "fetched_at" in cached
            else None
        )
        if cached is not None and age is not None and age < FRESH_FOR:
            hosts: set[str] = {str(h) for h in cached.get("hosts", [])}
        else:
            try:
                download = self.download or https_download
                hosts = parse(feed, download(feed.url))
            except StealthError:
                if cached is None or age is None or age >= USABLE_FOR:
                    raise BlocklistUnavailable(
                        f"Stealth couldn't download the {feed.name} list of scam and phishing "
                        "sites to check against."
                    ) from None
                hosts = {str(h) for h in cached.get("hosts", [])}
            else:
                self.folder.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({"fetched_at": now.isoformat(), "hosts": sorted(hosts)}),
                    encoding="utf-8",
                )
        self._hosts[feed.name] = hosts
        return hosts

    def listed(self, hosts: Iterable[str]) -> str | None:
        wanted = {host_of(h) for h in hosts if h}
        for feed in self.feeds:
            if wanted & self._load(feed):
                return feed.name
        return None


@dataclass
class FixedBlocklist:
    """A made-up list, for the demo and tests."""

    hosts: set[str] = field(default_factory=set)
    available: bool = True
    names: tuple[str, ...] = ("PhishTank", "OpenPhish")

    def listed(self, hosts: Iterable[str]) -> str | None:
        if not self.available:
            raise BlocklistUnavailable(
                "Stealth couldn't download the PhishTank list of scam and phishing sites to "
                "check against."
            )
        return "PhishTank" if {host_of(h) for h in hosts} & self.hosts else None
