"""Reminders and alerts: what Stealth wants the person to know, and when.

A reminder is something to come back to on a given day ("check your open register opt-out").
When its day comes, it raises an alert. Alerts show in the app until dismissed, and the tray
shows each one once. Any part of Stealth can add either: the checklist does now, and credit
reports, Cifas and breach monitoring will.

Dates are calendar dates where the person lives, like every other date in Stealth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from stealth.core import clock
from stealth.profiles.store import Profile


@dataclass(frozen=True)
class Reminder:
    topic: str
    title: str
    message: str
    link: str
    due_on: date
    every_days: int | None
    alerted_on: date | None


@dataclass(frozen=True)
class Alert:
    id: int
    topic: str
    title: str
    message: str
    link: str
    created_at: str
    notified_at: str | None
    dismissed_at: str | None


def _reminder(row: object) -> Reminder:
    data = dict(row)  # type: ignore[call-overload]
    alerted = data["alerted_on"]
    return Reminder(
        topic=data["topic"],
        title=data["title"],
        message=data["message"],
        link=data["link"],
        due_on=date.fromisoformat(data["due_on"]),
        every_days=data["every_days"],
        alerted_on=date.fromisoformat(alerted) if alerted else None,
    )


def _alert(row: object) -> Alert:
    return Alert(**dict(row))  # type: ignore[call-overload]


# Reminders -----------------------------------------------------------------------------------


def remind(
    profile: Profile,
    topic: str,
    *,
    title: str,
    message: str,
    due_on: date,
    link: str = "",
    every_days: int | None = None,
) -> Reminder:
    """Set (or move) the reminder for `topic`. One reminder per topic."""
    profile.conn.execute(
        """INSERT INTO reminder (topic, title, message, link, due_on, every_days, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (topic) DO UPDATE SET title = excluded.title, message = excluded.message,
               link = excluded.link, due_on = excluded.due_on, every_days = excluded.every_days,
               alerted_on = NULL""",
        (topic, title, message, link, due_on.isoformat(), every_days, clock.now().isoformat()),
    )
    reminder = get_reminder(profile, topic)
    assert reminder is not None
    return reminder


def get_reminder(profile: Profile, topic: str) -> Reminder | None:
    row = profile.conn.execute("SELECT * FROM reminder WHERE topic = ?", (topic,)).fetchone()
    return _reminder(row) if row is not None else None


def reminders(profile: Profile, prefix: str = "") -> list[Reminder]:
    rows = profile.conn.execute(
        "SELECT * FROM reminder WHERE topic LIKE ? ESCAPE '\\' ORDER BY due_on, topic",
        (_like_prefix(prefix),),
    )
    return [_reminder(r) for r in rows]


def forget_reminder(profile: Profile, topic: str) -> None:
    profile.conn.execute("DELETE FROM reminder WHERE topic = ?", (topic,))


def raise_due(profile: Profile, today: date) -> list[Alert]:
    """Turn every reminder that has come due into an alert, once per due date."""
    raised = []
    for reminder in reminders(profile):
        if reminder.due_on > today or reminder.alerted_on == reminder.due_on:
            continue
        raised.append(
            add_alert(
                profile,
                reminder.topic,
                title=reminder.title,
                message=reminder.message,
                link=reminder.link,
            )
        )
        profile.conn.execute(
            "UPDATE reminder SET alerted_on = due_on WHERE topic = ?", (reminder.topic,)
        )
    return raised


def done(profile: Profile, topic: str, today: date) -> Reminder | None:
    """The person did the thing: a repeating reminder moves on, a one-off goes away.

    Its open alerts are dismissed either way, since they've been dealt with.
    """
    dismiss_topic(profile, topic)
    reminder = get_reminder(profile, topic)
    if reminder is None:
        return None
    if reminder.every_days is None:
        forget_reminder(profile, topic)
        return None
    return remind(
        profile,
        topic,
        title=reminder.title,
        message=reminder.message,
        link=reminder.link,
        due_on=today + timedelta(days=reminder.every_days),
        every_days=reminder.every_days,
    )


# Alerts --------------------------------------------------------------------------------------


def add_alert(profile: Profile, topic: str, *, title: str, message: str, link: str = "") -> Alert:
    cursor = profile.conn.execute(
        """INSERT INTO alert (topic, title, message, link, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (topic, title, message, link, clock.now().isoformat()),
    )
    row = profile.conn.execute("SELECT * FROM alert WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _alert(row)


def open_alerts(profile: Profile, prefix: str = "") -> list[Alert]:
    """Alerts not yet dismissed, newest first."""
    rows = profile.conn.execute(
        """SELECT * FROM alert WHERE dismissed_at IS NULL AND topic LIKE ? ESCAPE '\\'
           ORDER BY created_at DESC, id DESC""",
        (_like_prefix(prefix),),
    )
    return [_alert(r) for r in rows]


def to_notify(profile: Profile) -> list[Alert]:
    """Open alerts the tray hasn't shown yet, oldest first."""
    rows = profile.conn.execute(
        """SELECT * FROM alert WHERE dismissed_at IS NULL AND notified_at IS NULL
           ORDER BY created_at, id"""
    )
    return [_alert(r) for r in rows]


def mark_notified(profile: Profile, alert_ids: list[int]) -> None:
    now = clock.now().isoformat()
    profile.conn.executemany(
        "UPDATE alert SET notified_at = ? WHERE id = ? AND notified_at IS NULL",
        [(now, alert_id) for alert_id in alert_ids],
    )


def dismiss(profile: Profile, alert_id: int) -> None:
    profile.conn.execute(
        "UPDATE alert SET dismissed_at = ? WHERE id = ? AND dismissed_at IS NULL",
        (clock.now().isoformat(), alert_id),
    )


def dismiss_topic(profile: Profile, topic: str) -> None:
    profile.conn.execute(
        "UPDATE alert SET dismissed_at = ? WHERE topic = ? AND dismissed_at IS NULL",
        (clock.now().isoformat(), topic),
    )


def _like_prefix(prefix: str) -> str:
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"
