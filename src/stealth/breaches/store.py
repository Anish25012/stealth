"""Recording breaches, raising alerts for new ones, and the automatic check's cycle.

A breach is recorded once per address. A new one raises an alert (shown in the app and once in
the tray) and counts against the privacy score until the person says they've dealt with it,
for example by changing that password.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from stealth import alerts
from stealth.breaches.hibp import PAUSE_SECONDS
from stealth.breaches.provider import Breach, BreachProvider
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.store import Profile
from stealth.templates.render import format_date

TOPIC = "breach:"
HIBP = "hibp"
MANUAL = "manual"
PROVIDERS = (HIBP, MANUAL)
EVERY_CHOICES: tuple[tuple[int, str], ...] = (
    (7, "Every week"),
    (30, "Every month"),
    (91, "Every three months"),
)
KEY_PATTERN = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class Found:
    id: int
    email: str
    breach: Breach
    source: str
    found_at: str
    dealt_with_at: str | None

    @property
    def dealt_with(self) -> bool:
        return self.dealt_with_at is not None


@dataclass(frozen=True)
class CheckSummary:
    checked: int
    new: list[Found]

    @property
    def line(self) -> str:
        addresses = f"{self.checked} address{'' if self.checked == 1 else 'es'}"
        if not self.new:
            return f"Checked {addresses}: no new breaches."
        n = len(self.new)
        return f"Checked {addresses}: {n} new breach{'' if n == 1 else 'es'} found."


# The API key ----------------------------------------------------------------------------------


def key_name(profile: Profile) -> str:
    return f"hibp-api-key:{profile.id}"


def has_key(profile: Profile) -> bool:
    return bool(profile.secrets.get(key_name(profile)))


def api_key(profile: Profile) -> str | None:
    return profile.secrets.get(key_name(profile))


def save_key(profile: Profile, key: str) -> None:
    """Keep the key in the keychain. HIBP keys are 32 hexadecimal characters."""
    clean = "".join(key.split()).lower()
    if not KEY_PATTERN.fullmatch(clean):
        raise ProfileError(
            "That doesn't look like a Have I Been Pwned API key.",
            hint="It's 32 letters and numbers, from your HIBP dashboard.",
        )
    profile.secrets.set(key_name(profile), clean)


def forget_key(profile: Profile) -> None:
    profile.secrets.delete(key_name(profile))


# Settings -------------------------------------------------------------------------------------


def configure(profile: Profile, *, on: bool, provider: str, every_days: int) -> None:
    if provider not in PROVIDERS:
        raise ProfileError("Choose how to check for breaches.")
    if every_days not in {d for d, _label in EVERY_CHOICES}:
        raise ProfileError("Choose how often to check from the list.")
    profile.save_settings(
        breach_monitoring=on, breach_provider=provider, breach_every_days=every_days
    )


def due(profile: Profile, now: datetime) -> bool:
    """Whether the automatic check should run: on, using HIBP, with a key, and its time has come."""
    settings = profile.settings()
    if not (settings.breach_monitoring and settings.breach_provider == HIBP and has_key(profile)):
        return False
    if settings.last_breach_check is None:
        return True
    last = datetime.fromisoformat(settings.last_breach_check)
    return now - last >= timedelta(days=settings.breach_every_days)


def addresses(profile: Profile) -> list[str]:
    """The person's email addresses, each once, in lower case."""
    seen: dict[str, None] = {}
    for email in profile.person().emails:
        clean = email.strip().lower()
        if clean:
            seen.setdefault(clean, None)
    return list(seen)


# Recording ------------------------------------------------------------------------------------


def _found(row: object) -> Found:
    data = dict(row)  # type: ignore[call-overload]
    return Found(
        id=data["id"],
        email=data["email"],
        breach=Breach(
            name=data["name"],
            title=data["title"],
            domain=data["domain"],
            breach_date=date.fromisoformat(data["breach_date"]) if data["breach_date"] else None,
            data_classes=tuple(json.loads(data["data_classes"])),
            description=data["description"],
            verified=bool(data["verified"]),
        ),
        source=data["source"],
        found_at=data["found_at"],
        dealt_with_at=data["dealt_with_at"],
    )


def record(profile: Profile, email: str, breaches: Iterable[Breach], source: str) -> list[Found]:
    """Store breaches for `email`. Returns only the new ones, each with a new alert."""
    address = email.strip().lower()
    new = []
    for breach in breaches:
        cursor = profile.conn.execute(
            """INSERT INTO breach (email, name, title, domain, breach_date, data_classes,
                   description, verified, source, found_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (email, name) DO NOTHING""",
            (
                address,
                breach.name,
                breach.title,
                breach.domain,
                breach.breach_date.isoformat() if breach.breach_date else None,
                json.dumps(list(breach.data_classes)),
                breach.description,
                int(breach.verified),
                source,
                clock.now().isoformat(),
            ),
        )
        if cursor.rowcount != 1:
            continue
        found = get(profile, cursor.lastrowid)
        new.append(found)
        alerts.add_alert(
            profile,
            TOPIC + str(found.id),
            title=f"Your details were in the {breach.title} data breach",
            message=_alert_message(found),
            link=f"/protect#breach-{found.id}",
        )
    return new


def _alert_message(found: Found) -> str:
    when = f" in {format_date(found.breach.breach_date)}" if found.breach.breach_date else ""
    exposed = ", ".join(found.breach.data_classes[:4]).lower()
    what = f" It exposed {exposed}." if exposed else ""
    return (
        f"One of your email addresses was in a breach{when}.{what} Change that password, and "
        "anywhere you used the same one."
    )


def get(profile: Profile, found_id: int | None) -> Found:
    row = profile.conn.execute("SELECT * FROM breach WHERE id = ?", (found_id,)).fetchone()
    if row is None:
        raise ProfileError("Stealth doesn't know that breach.")
    return _found(row)


def all_found(profile: Profile) -> list[Found]:
    """Every breach, the ones still to deal with first, newest breach first within each."""
    rows = profile.conn.execute(
        """SELECT * FROM breach
           ORDER BY dealt_with_at IS NOT NULL, breach_date IS NULL, breach_date DESC, id DESC"""
    )
    return [_found(r) for r in rows]


def open_count(profile: Profile) -> int:
    """Breaches not yet dealt with: what the privacy score counts."""
    row = profile.conn.execute("SELECT count(*) FROM breach WHERE dealt_with_at IS NULL")
    return int(row.fetchone()[0])


def mark_dealt_with(profile: Profile, found_id: int, dealt_with: bool) -> None:
    found = get(profile, found_id)
    profile.conn.execute(
        "UPDATE breach SET dealt_with_at = ? WHERE id = ?",
        (clock.now().isoformat() if dealt_with else None, found.id),
    )
    if dealt_with:
        alerts.dismiss_topic(profile, TOPIC + str(found.id))


# Checking -------------------------------------------------------------------------------------


def run_check(
    profile: Profile, provider: BreachProvider, *, sleep: Callable[[float], None]
) -> CheckSummary:
    """Ask `provider` about every address and record what's new, then note when it ran.

    Stops at the first error, keeping what it already found. It doesn't note the time then: the
    caller decides when to try again (soon after a dropped connection, not until the key is fixed
    after a refused key).
    """
    emails = addresses(profile)
    if not emails:
        raise ProfileError(
            "There are no email addresses in your details to check.",
            hint="Add them in Settings, under your details.",
        )
    new: list[Found] = []
    for index, email in enumerate(emails):
        if index and provider.source == HIBP:
            sleep(PAUSE_SECONDS)
        new += record(profile, email, provider.check(email), provider.source)
    summary = CheckSummary(len(emails), new)
    profile.save_settings(
        last_breach_check=clock.now().isoformat(), last_breach_result=summary.line
    )
    return summary
