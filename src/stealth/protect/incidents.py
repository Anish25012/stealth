"""The incident log behind the recovery playbook: what happened, what was done, and references.

An incident is one situation from `recovery.SITUATIONS`, started on a date. Each action can be
ticked done, with a reference number and notes. Ticking one, or un-ticking it, adds a line to the
log, and the person can add their own lines (a call to the bank, a letter from a lender). It all
lives in the profile's encrypted database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.store import Profile
from stealth.protect import recovery

REFERENCE_LIMIT = 200
NOTE_LIMIT = 2000
OLDEST_YEARS = 10


@dataclass(frozen=True)
class Progress:
    action: recovery.Action
    done_on: date | None
    reference: str
    note: str

    @property
    def done(self) -> bool:
        return self.done_on is not None


@dataclass(frozen=True)
class Entry:
    id: int
    happened_on: date
    text: str
    reference: str


@dataclass(frozen=True)
class Incident:
    id: int
    situation: recovery.Situation
    started_on: date
    closed_on: date | None
    progress: tuple[Progress, ...]
    entries: tuple[Entry, ...]

    @property
    def done(self) -> int:
        return sum(p.done for p in self.progress)

    @property
    def total(self) -> int:
        return len(self.progress)

    @property
    def next_action(self) -> Progress | None:
        return next((p for p in self.progress if not p.done), None)


def _situation(situation_id: str) -> recovery.Situation:
    situation = recovery.BY_ID.get(situation_id)
    if situation is None:
        raise ProfileError("Choose what's happened from the list.")
    return situation


def _check_date(when: date, today: date, what: str) -> None:
    if when > today:
        raise ProfileError(f"The {what} can't be in the future.")
    if when.year < today.year - OLDEST_YEARS:
        raise ProfileError(f"Check the {what}: it's more than {OLDEST_YEARS} years ago.")


def _clean(text: str, limit: int, what: str) -> str:
    clean = text.strip()
    if len(clean) > limit:
        raise ProfileError(f"Keep the {what} under {limit} characters.")
    return clean


def start(profile: Profile, situation_id: str, started_on: date, today: date) -> int:
    situation = _situation(situation_id)
    _check_date(started_on, today, "date it happened")
    now = clock.now().isoformat()
    cursor = profile.conn.execute(
        "INSERT INTO incident (situation, started_on, created_at) VALUES (?, ?, ?)",
        (situation.id, started_on.isoformat(), now),
    )
    incident_id = int(cursor.lastrowid or 0)
    _log(profile, incident_id, today, f"Started: {situation.title}.")
    return incident_id


def _log(profile: Profile, incident_id: int, when: date, text: str, reference: str = "") -> None:
    profile.conn.execute(
        "INSERT INTO incident_entry (incident_id, happened_on, text, reference, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (incident_id, when.isoformat(), text, reference, clock.now().isoformat()),
    )


def get(profile: Profile, incident_id: int) -> Incident:
    row = profile.conn.execute("SELECT * FROM incident WHERE id = ?", (incident_id,)).fetchone()
    if row is None:
        raise ProfileError("Stealth doesn't know that incident.")
    situation = recovery.BY_ID.get(row["situation"])
    if situation is None:  # from a newer Stealth, or a situation since retired
        raise ProfileError("This version of Stealth doesn't know that situation.")
    saved = {
        r["action_id"]: r
        for r in profile.conn.execute(
            "SELECT * FROM incident_action WHERE incident_id = ?", (incident_id,)
        )
    }
    progress = []
    for action in situation.actions:
        r = saved.get(action.id)
        progress.append(
            Progress(
                action,
                date.fromisoformat(r["done_on"]) if r is not None and r["done_on"] else None,
                r["reference"] if r is not None else "",
                r["note"] if r is not None else "",
            )
        )
    entries = tuple(
        Entry(e["id"], date.fromisoformat(e["happened_on"]), e["text"], e["reference"])
        for e in profile.conn.execute(
            "SELECT * FROM incident_entry WHERE incident_id = ? ORDER BY happened_on, id",
            (incident_id,),
        )
    )
    return Incident(
        row["id"],
        situation,
        date.fromisoformat(row["started_on"]),
        date.fromisoformat(row["closed_on"]) if row["closed_on"] else None,
        tuple(progress),
        entries,
    )


def all_incidents(profile: Profile) -> list[Incident]:
    """Open incidents first, newest first. Ones from a situation this Stealth doesn't know are
    left out rather than breaking the page."""
    rows = profile.conn.execute(
        "SELECT id, situation FROM incident"
        " ORDER BY closed_on IS NOT NULL, started_on DESC, id DESC"
    ).fetchall()
    return [get(profile, r["id"]) for r in rows if r["situation"] in recovery.BY_ID]


def save_action(
    profile: Profile,
    incident_id: int,
    action_id: str,
    *,
    done: bool,
    reference: str,
    note: str,
    today: date,
) -> None:
    incident = get(profile, incident_id)
    current = next((p for p in incident.progress if p.action.id == action_id), None)
    if current is None:
        raise ProfileError("That step isn't part of this situation.")
    reference = _clean(reference, REFERENCE_LIMIT, "reference")
    note = _clean(note, NOTE_LIMIT, "notes")
    done_on = (current.done_on or today) if done else None
    profile.conn.execute(
        """INSERT INTO incident_action (incident_id, action_id, done_on, reference, note,
               updated_at) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (incident_id, action_id) DO UPDATE SET done_on = excluded.done_on,
               reference = excluded.reference, note = excluded.note,
               updated_at = excluded.updated_at""",
        (
            incident.id,
            action_id,
            done_on.isoformat() if done_on else None,
            reference,
            note,
            clock.now().isoformat(),
        ),
    )
    title = current.action.title
    if done and not current.done:
        _log(profile, incident.id, today, f"Done: {title}.", reference)
    elif not done and current.done:
        _log(profile, incident.id, today, f"Marked not done: {title}.")
    elif reference and reference != current.reference:
        _log(profile, incident.id, today, f"Reference noted: {title}.", reference)


def add_entry(
    profile: Profile, incident_id: int, happened_on: date, text: str, reference: str, today: date
) -> None:
    incident = get(profile, incident_id)
    _check_date(happened_on, today, "date")
    text = _clean(text, NOTE_LIMIT, "entry")
    if not text:
        raise ProfileError("Write what happened.")
    _log(profile, incident.id, happened_on, text, _clean(reference, REFERENCE_LIMIT, "reference"))


def delete_entry(profile: Profile, incident_id: int, entry_id: int) -> None:
    get(profile, incident_id)
    profile.conn.execute(
        "DELETE FROM incident_entry WHERE id = ? AND incident_id = ?", (entry_id, incident_id)
    )


def close(profile: Profile, incident_id: int, today: date, *, closed: bool) -> None:
    incident = get(profile, incident_id)
    profile.conn.execute(
        "UPDATE incident SET closed_on = ? WHERE id = ?",
        (today.isoformat() if closed else None, incident.id),
    )
    _log(profile, incident.id, today, "Marked as sorted." if closed else "Reopened.")


def delete(profile: Profile, incident_id: int) -> None:
    incident = get(profile, incident_id)
    for table in ("incident_entry", "incident_action"):
        profile.conn.execute(f"DELETE FROM {table} WHERE incident_id = ?", (incident.id,))  # noqa: S608 - fixed names
    profile.conn.execute("DELETE FROM incident WHERE id = ?", (incident.id,))
