"""What the assisted scan found, and what Stealth does about it.

Each capture is kept as evidence (the screenshot's SHA-256 is recorded, like every email) and waits
for the person to say whether it shows them. Only a person can say "that's me": names are shared by
many people, and writing to a company about someone else's listing would hand it new data.

"That's me" on a company Stealth knows opens a case with the listing's address in the letter, or,
if the company already said it had removed the person, starts a new letter: they've re-listed them.
"Not me" or "no results" is recorded too: after a removal, it's the proof the listing has gone.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from stealth.cases.engine import _create_for, jurisdiction_of
from stealth.cases.state import TRANSITIONS, Case, Status, all_cases, transition
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.core.paths import ensure_private_dir
from stealth.custom.email_extract import registrable
from stealth.db import events
from stealth.profiles.store import Profile
from stealth.registry.loader import Broker, Selection
from stealth.scan.assisted import Capture

LISTED, NOT_LISTED, PENDING = "listed", "not_listed", "pending"
DONE = (Status.REMOVED, Status.NO_DATA)


@dataclass(frozen=True)
class ScanSite:
    """A site worth checking, and the addresses its pages can come from."""

    broker_id: str
    name: str
    start_url: str
    hosts: tuple[str, ...]  # host names (or host:port, for the demo) that belong to it


@dataclass(frozen=True)
class ScanResult:
    id: int
    site: str
    broker_id: str | None
    url: str
    title: str
    outcome: str
    artifact_id: int | None
    captured_at: datetime
    decided_at: datetime | None


def suggested_sites(brokers: dict[str, Broker]) -> list[ScanSite]:
    """UK people-search sites: where a name, address and phone number are most often on show."""
    sites = []
    for broker in sorted(brokers.values(), key=lambda b: b.name.lower()):
        if broker.category == "people-search" and broker.region == "UK":
            sites.append(
                ScanSite(broker.id, broker.name, f"https://{broker.domain}/", broker.all_domains)
            )
    return sites


def _host(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return f"{host}:{parts.port}" if parts.port else host


def company_for(url: str, brokers: dict[str, Broker], sites: list[ScanSite]) -> str | None:
    """The company whose site a captured page is on, by its address, never by its content."""
    host = _host(url)
    for site in sites:
        if host in site.hosts:
            return site.broker_id
    bare = host.split(":", 1)[0]
    if not bare:
        return None
    own = registrable(bare)
    for broker in brokers.values():
        if any(registrable(d) == own for d in (broker.domain, *broker.reply_domains)):
            return broker.id
    return None


def _row(row: object) -> ScanResult:
    data = dict(row)  # type: ignore[call-overload]
    data["captured_at"] = datetime.fromisoformat(data["captured_at"])
    if data["decided_at"]:
        data["decided_at"] = datetime.fromisoformat(data["decided_at"])
    return ScanResult(**data)


def record(profile: Profile, capture: Capture, broker_id: str | None) -> ScanResult:
    """Keep a capture as evidence. It waits for the person to say what it shows."""
    host = _host(capture.url) or "page"
    folder = ensure_private_dir(profile.evidence / "scans")
    stamp = clock.now().strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"{stamp}-{re.sub(r'[^a-z0-9.]+', '-', host)}.png"
    path.write_bytes(capture.png)
    now = clock.now().isoformat()
    artifact = profile.conn.execute(
        "INSERT INTO artifact (case_id, kind, path, sha256, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            None,
            "scan",
            str(path.relative_to(profile.folder)),
            hashlib.sha256(capture.png).hexdigest(),
            now,
        ),
    ).lastrowid
    result_id = profile.conn.execute(
        "INSERT INTO scan_result (site, broker_id, url, title, outcome, artifact_id, captured_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (host, broker_id, capture.url, capture.title[:200], artifact, now),
    ).lastrowid
    events.append(profile.conn, "scan_captured", url=capture.url, broker=broker_id)
    return get(profile, int(result_id))


def get(profile: Profile, result_id: int) -> ScanResult:
    row = profile.conn.execute("SELECT * FROM scan_result WHERE id = ?", (result_id,)).fetchone()
    if row is None:
        raise ProfileError("Stealth has no capture with that number.")
    return _row(row)


def all_results(profile: Profile) -> list[ScanResult]:
    rows = profile.conn.execute("SELECT * FROM scan_result ORDER BY captured_at DESC, id DESC")
    return [_row(r) for r in rows]


def image_path(profile: Profile, result: ScanResult) -> str:
    row = profile.conn.execute(
        "SELECT path FROM artifact WHERE id = ?", (result.artifact_id,)
    ).fetchone()
    if row is None:
        raise ProfileError("That capture has no picture.")
    return str(row["path"])


@dataclass(frozen=True)
class Decision:
    result: ScanResult
    case: Case | None  # the case it opened or restarted, if any
    action: str  # what happened, in a sentence


def decide(profile: Profile, result_id: int, outcome: str, brokers: dict[str, Broker]) -> Decision:
    if outcome not in (LISTED, NOT_LISTED):
        raise ProfileError("Choose whether the page shows you.")
    result = get(profile, result_id)
    if result.outcome != PENDING:
        raise ProfileError("You've already said what that capture shows.")
    profile.conn.execute(
        "UPDATE scan_result SET outcome = ?, decided_at = ? WHERE id = ?",
        (outcome, clock.now().isoformat(), result_id),
    )
    result = get(profile, result_id)
    events.append(profile.conn, "scan_decided", url=result.url, outcome=outcome)
    broker = brokers.get(result.broker_id or "")
    if outcome == NOT_LISTED:
        return Decision(result, None, "Recorded: this page doesn't show you.")
    if broker is None:
        return Decision(result, None, "Recorded. Stealth doesn't know this company yet.")
    return _act_on_listing(profile, broker, result)


def _act_on_listing(profile: Profile, broker: Broker, result: ScanResult) -> Decision:
    case = next((c for c in all_cases(profile.conn) if c.broker_id == broker.id), None)
    if case is None:
        jurisdiction = jurisdiction_of(profile)
        core = broker.tier == "core" and bool(jurisdiction.law_tags & set(broker.laws))
        case = _create_for(profile, Selection(broker, "core" if core else "extended"), hold=True)
        case = _set_listing(profile, case, result.url)
        action = (
            f"A letter to {broker.name} is waiting for your OK."
            if case.status is Status.DRAFT
            else f"{broker.name} only takes requests through its website."
        )
        return Decision(result, case, action)
    case = _set_listing(profile, case, result.url)
    if case.status in DONE and Status.DRAFT in TRANSITIONS[case.status]:
        case = transition(
            profile.conn,
            case,
            Status.DRAFT,
            reason="listed again after removal",
        )
        return Decision(
            result,
            case,
            f"{broker.name} has listed you again. A new letter is waiting for your OK.",
        )
    return Decision(result, case, f"Saved with {broker.name}'s case. Its letter names the page.")


def _set_listing(profile: Profile, case: Case, url: str) -> Case:
    profile.conn.execute("UPDATE cases SET custom_url = ? WHERE id = ?", (url, case.id))
    events.append(profile.conn, "listing_found", case_id=case.id, url=url)
    return next(c for c in all_cases(profile.conn) if c.id == case.id)


# Where things stand, per company --------------------------------------------------------------


@dataclass(frozen=True)
class SiteStatus:
    label: str
    tone: str  # badge tone
    last: ScanResult | None


def status_of(profile: Profile, broker_id: str) -> SiteStatus:
    decided = [r for r in all_results(profile) if r.broker_id == broker_id and r.outcome != PENDING]
    if not decided:
        return SiteStatus("Not checked", "neutral", None)
    last = decided[0]
    case = next((c for c in all_cases(profile.conn) if c.broker_id == broker_id), None)
    if last.outcome == LISTED:
        if case and case.status in DONE:
            return SiteStatus("Check it's gone", "action", last)
        return SiteStatus("You're listed", "problem", last)
    if any(r.outcome == LISTED for r in decided[1:]):
        return SiteStatus("Gone: checked", "good", last)
    return SiteStatus("Not listed", "good", last)


def open_listings(profile: Profile) -> int:
    """Pages the person said show them, with no later check saying they've gone."""
    latest: dict[str, str] = {}
    for result in reversed(all_results(profile)):  # oldest first, so the newest wins
        if result.outcome != PENDING:
            latest[result.broker_id or result.site] = result.outcome
    return sum(1 for outcome in latest.values() if outcome == LISTED)


def ever_scanned(profile: Profile) -> bool:
    return any(r.outcome != PENDING for r in all_results(profile))
