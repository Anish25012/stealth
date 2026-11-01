"""The only place the engine reads the time.

Everything legal here depends on dates, so tests must be able to move time. All engine code calls
`clock.now()` or `clock.local_date()`; nothing else touches `datetime.now`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

_frozen: datetime | None = None


def now() -> datetime:
    """Current time as an aware UTC datetime."""
    return _frozen if _frozen is not None else datetime.now(UTC)


def local_date(tz: str) -> date:
    """Today's date where the person lives.

    Deadlines are calendar dates in the person's own time zone: an email sent at 00:30 on 1 June
    UK time is 23:30 on 31 May in UTC, and must count as 1 June.
    """
    return to_local_date(now(), tz)


def to_local_date(moment: datetime, tz: str) -> date:
    """The calendar date of `moment` in time zone `tz`."""
    return moment.astimezone(ZoneInfo(tz)).date()


def set_time(moment: datetime) -> None:
    """Freeze the clock. Used by tests and by the demo's fast-forward."""
    if moment.tzinfo is None:
        raise ValueError("clock.set_time needs an aware datetime")
    global _frozen
    _frozen = moment.astimezone(UTC)


def advance(delta: timedelta) -> None:
    """Move the clock forward, freezing it at real time first if it was running."""
    set_time(now() + delta)


def reset() -> None:
    """Return to real time."""
    global _frozen
    _frozen = None


@contextmanager
def frozen_at(moment: datetime) -> Iterator[None]:
    """Freeze the clock for a block, then restore whatever was there before."""
    previous = _frozen
    set_time(moment)
    try:
        yield
    finally:
        if previous is None:
            reset()
        else:
            set_time(previous)
