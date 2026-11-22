"""Where the person is with the checklist: done, not for them, reminders, and a score.

The checklist score is separate from the privacy score: it measures what the person has done to
protect themselves, not what companies hold about them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from stealth import alerts
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.profiles.store import Profile
from stealth.protect.checklist import BY_ID, ITEMS, Item

DONE = "done"
NOT_FOR_ME = "not_for_me"
TOPIC = "protect:"

# What the person can pick for "remind me". Days, with the words shown.
REMIND_CHOICES: tuple[tuple[int, str], ...] = (
    (7, "In a week"),
    (30, "In a month"),
    (91, "In three months"),
    (182, "In six months"),
    (365, "In a year"),
)


@dataclass(frozen=True)
class Entry:
    item: Item
    state: str | None  # DONE, NOT_FOR_ME, or None while still to do
    marked_at: str | None
    reminder: alerts.Reminder | None

    @property
    def done(self) -> bool:
        return self.state == DONE


@dataclass(frozen=True)
class Score:
    done: int
    counted: int  # items that apply: everything except "not for me"

    @property
    def percent(self) -> int:
        return round(100 * self.done / self.counted) if self.counted else 100


def topic(item_id: str) -> str:
    return TOPIC + item_id


def _item(item_id: str) -> Item:
    try:
        return BY_ID[item_id]
    except KeyError:
        raise ProfileError("That isn't on the checklist.") from None


def entries(profile: Profile) -> list[Entry]:
    marks = {
        row["id"]: (row["state"], row["marked_at"])
        for row in profile.conn.execute("SELECT * FROM protect_item")
    }
    due = {r.topic: r for r in alerts.reminders(profile, TOPIC)}
    return [
        Entry(item, *marks.get(item.id, (None, None)), due.get(topic(item.id))) for item in ITEMS
    ]


def score(profile: Profile) -> Score:
    all_entries = entries(profile)
    counted = [e for e in all_entries if e.state != NOT_FOR_ME]
    return Score(done=sum(e.done for e in counted), counted=len(counted))


def mark(profile: Profile, item_id: str, state: str | None, today: date) -> None:
    """Mark an item done, not for me, or back to do (None).

    Done clears its alerts and, for items worth checking again, sets a reminder to recheck.
    """
    item = _item(item_id)
    if state not in (DONE, NOT_FOR_ME, None):
        raise ProfileError("Choose done, not for me, or still to do.")
    if state is None:
        profile.conn.execute("DELETE FROM protect_item WHERE id = ?", (item.id,))
        return
    profile.conn.execute(
        """INSERT INTO protect_item (id, state, marked_at) VALUES (?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET state = excluded.state, marked_at = excluded.marked_at""",
        (item.id, state, clock.now().isoformat()),
    )
    alerts.dismiss_topic(profile, topic(item.id))
    if state == NOT_FOR_ME:
        alerts.forget_reminder(profile, topic(item.id))
    elif item.recheck_days:
        set_reminder(profile, item.id, item.recheck_days, today, repeat=True)
    else:
        alerts.forget_reminder(profile, topic(item.id))


def set_reminder(
    profile: Profile, item_id: str, days: int, today: date, *, repeat: bool = False
) -> alerts.Reminder:
    item = _item(item_id)
    if days not in {d for d, _label in REMIND_CHOICES} and days != item.recheck_days:
        raise ProfileError("Choose when to be reminded from the list.")
    if _state(profile, item.id) == DONE:
        message = "Time to check this again. " + (item.recheck_text or "")
    else:
        message = "You asked Stealth to remind you about this."
    return alerts.remind(
        profile,
        topic(item.id),
        title=item.title,
        message=message.strip(),
        link=f"/protect#{item.id}",
        due_on=today + timedelta(days=days),
        every_days=days if repeat else None,
    )


def clear_reminder(profile: Profile, item_id: str) -> None:
    item = _item(item_id)
    alerts.forget_reminder(profile, topic(item.id))
    alerts.dismiss_topic(profile, topic(item.id))


def _state(profile: Profile, item_id: str) -> str | None:
    row = profile.conn.execute("SELECT state FROM protect_item WHERE id = ?", (item_id,)).fetchone()
    return row["state"] if row is not None else None
