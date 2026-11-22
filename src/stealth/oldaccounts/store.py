"""The companies found, the person's Keep or Delete choice for each, and how far back it's read.

Choices are kept, so scanning again only puts new companies in front of the person. Companies
already decided stay listed separately and can be changed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.custom.email_extract import registrable
from stealth.oldaccounts.classify import Header, SignUp, classify
from stealth.oldaccounts.gmail import BATCH, SignupSource
from stealth.profiles.store import Profile

KEEP = "keep"
DELETE = "delete"


@dataclass(frozen=True)
class OldAccount:
    domain: str
    name: str
    first_seen_on: date
    last_seen_on: date
    decision: str | None
    deletion_url: str | None
    deletion_checked: bool


@dataclass(frozen=True)
class ScanState:
    scanned_at: str | None
    oldest_on: date | None
    more: bool


@dataclass(frozen=True)
class ScanSummary:
    read: int
    signups: int
    new: int
    oldest: date | None
    more: bool


def _row(r: object) -> OldAccount:
    d = dict(r)  # type: ignore[call-overload]
    return OldAccount(
        domain=d["domain"],
        name=d["name"],
        first_seen_on=date.fromisoformat(d["first_seen_on"]),
        last_seen_on=date.fromisoformat(d["last_seen_on"]),
        decision=d["decision"],
        deletion_url=d["deletion_url"],
        deletion_checked=d["deletion_checked_at"] is not None,
    )


def own_domains(profile: Profile) -> frozenset[str]:
    """The person's own email domains, so mail from themselves is never a 'company'."""
    found = set()
    for address in profile.person().emails:
        host = address.rpartition("@")[2].strip().lower()
        if host:
            found.add(registrable(host))
    account = profile.mail_account()
    if account is not None and "@" in account.address:
        found.add(registrable(account.address.rpartition("@")[2].lower()))
    return frozenset(found)


def companies(headers: Iterable[Header], own: frozenset[str] = frozenset()) -> list[SignUp]:
    """One entry per company: the oldest sign-up email's date, and the newest name seen."""
    earliest: dict[str, SignUp] = {}
    for header in headers:
        found = classify(header, own)
        if found is None:
            continue
        known = earliest.get(found.domain)
        if known is None or found.received_on < known.received_on:
            earliest[found.domain] = found
    return sorted(earliest.values(), key=lambda s: (s.received_on, s.domain))


def record(profile: Profile, headers: Iterable[Header]) -> tuple[int, int]:
    """Store what the headers show. Returns (sign-up companies seen, new companies)."""
    own = own_domains(profile)
    found: dict[str, list[SignUp]] = {}
    for header in headers:
        hit = classify(header, own)
        if hit is not None:
            found.setdefault(hit.domain, []).append(hit)
    new = 0
    for domain, hits in sorted(found.items()):
        first = min(hits, key=lambda s: s.received_on)
        last = max(h.received_on for h in hits)
        known = profile.conn.execute(
            "SELECT 1 FROM old_account WHERE domain = ?", (domain,)
        ).fetchone()
        profile.conn.execute(
            """INSERT INTO old_account (domain, name, first_seen_on, last_seen_on, found_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (domain) DO UPDATE SET
                   first_seen_on = min(first_seen_on, excluded.first_seen_on),
                   last_seen_on = max(last_seen_on, excluded.last_seen_on)""",
            (
                domain,
                first.name,
                first.received_on.isoformat(),
                last.isoformat(),
                clock.now().isoformat(),
            ),
        )
        new += known is None
    return len(found), new


def run_scan(profile: Profile, source: SignupSource, *, further_back: bool) -> ScanSummary:
    """Read one batch of sign-up style messages and record the companies.

    The first scan (and any "scan again") reads the newest messages. "Scan further back" carries
    on from the oldest message reached so far.
    """
    state = scan_state(profile)
    if further_back and state.oldest_on is None:
        raise ProfileError("Scan your Gmail first, then you can go further back.")
    batch = source.fetch(state.oldest_on if further_back else None, BATCH)
    read = list(batch.headers)
    if (
        further_back
        and state.oldest_on is not None
        and batch.more
        and (batch.oldest is None or batch.oldest >= state.oldest_on)
    ):
        # A whole batch from the oldest day already reached, so it got no further. Skip past
        # that day, so "Scan further back" always moves back or finishes, never repeats.
        batch = source.fetch(state.oldest_on - timedelta(days=1), BATCH)
        read += batch.headers
    seen, new = record(profile, read)
    reached_further = (
        further_back
        or state.oldest_on is None
        or (batch.oldest is not None and batch.oldest <= state.oldest_on)
    )
    if reached_further:
        oldest, more = batch.oldest or state.oldest_on, batch.more
    else:  # scanning recent mail again doesn't undo having read further back
        oldest, more = state.oldest_on, state.more
    profile.conn.execute(
        """INSERT INTO old_account_scan (id, scanned_at, oldest_on, more) VALUES (1, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET scanned_at = excluded.scanned_at,
               oldest_on = excluded.oldest_on, more = excluded.more""",
        (clock.now().isoformat(), oldest.isoformat() if oldest else None, int(more)),
    )
    return ScanSummary(len(read), seen, new, oldest, more)


def scan_state(profile: Profile) -> ScanState:
    row = profile.conn.execute("SELECT * FROM old_account_scan WHERE id = 1").fetchone()
    if row is None:
        return ScanState(None, None, False)
    oldest = date.fromisoformat(row["oldest_on"]) if row["oldest_on"] else None
    return ScanState(row["scanned_at"], oldest, bool(row["more"]))


def undecided(profile: Profile) -> list[OldAccount]:
    rows = profile.conn.execute(
        "SELECT * FROM old_account WHERE decision IS NULL ORDER BY first_seen_on, domain"
    )
    return [_row(r) for r in rows]


def decided(profile: Profile) -> list[OldAccount]:
    rows = profile.conn.execute(
        "SELECT * FROM old_account WHERE decision IS NOT NULL ORDER BY decided_at DESC, domain"
    )
    return [_row(r) for r in rows]


def get(profile: Profile, domain: str) -> OldAccount:
    row = profile.conn.execute(
        "SELECT * FROM old_account WHERE domain = ?", (domain.strip().lower(),)
    ).fetchone()
    if row is None:
        raise ProfileError("Stealth didn't find that company in your Gmail.")
    return _row(row)


def decide(profile: Profile, domain: str, decision: str | None) -> OldAccount:
    if decision not in (KEEP, DELETE, None):
        raise ProfileError("Choose Keep or Delete my data.")
    account = get(profile, domain)
    profile.conn.execute(
        "UPDATE old_account SET decision = ?, decided_at = ? WHERE domain = ?",
        (decision, clock.now().isoformat() if decision else None, account.domain),
    )
    return get(profile, account.domain)


def save_deletion_page(profile: Profile, domain: str, url: str | None) -> None:
    account = get(profile, domain)
    profile.conn.execute(
        "UPDATE old_account SET deletion_url = ?, deletion_checked_at = ? WHERE domain = ?",
        (url, clock.now().isoformat(), account.domain),
    )


def forget_all(profile: Profile) -> None:
    """Clear the list and the scan's progress, for example before handing a computer on."""
    profile.conn.execute("DELETE FROM old_account")
    profile.conn.execute("DELETE FROM old_account_scan")
