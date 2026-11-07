"""Running by itself: one cycle a day for each person, with catch-up after the computer was off.

The agent starts this when it starts. Every few minutes it looks at each profile: if today's
automatic check hasn't happened, and either it's past the chosen hour or a whole day was missed,
it runs a cycle. That's the same cycle as the "Run cycle now" button: it reads replies, chases late
companies with formal complaints, and sends only letters the person has already approved.

It never touches the demo, never runs two things at once (it takes the same lock as the buttons),
and tells the person through the tray when something needs them.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from stealth import cycle
from stealth.core import clock
from stealth.core.errors import MailAuthError, MailConnectionError, StealthError
from stealth.core.logging_scrub import add_known_values
from stealth.profiles.models import Settings
from stealth_app import monthly, updates, views
from stealth_app.workspace import Workspace

log = logging.getLogger("stealth.scheduler")

TICK_SECONDS = 300  # how often to look; a check is due at most once a day
STARTUP_GRACE = timedelta(seconds=90)  # let the network come up after the computer starts
RETRY_AFTER = timedelta(minutes=30)  # after "couldn't connect", before trying again

Notify = Callable[[str, str], None]  # (title, message)


def local_now() -> datetime:
    """Now, in this computer's own time zone: "9am" means 9am on the clock on the wall."""
    return clock.now().astimezone()


def is_due(settings: Settings, now: datetime) -> bool:
    """Whether today's automatic check should run now."""
    if not settings.auto_run:
        return False
    if settings.last_auto_run is None:
        return True  # newly set up: check straight away
    last = datetime.fromisoformat(settings.last_auto_run).astimezone(now.tzinfo).date()
    today = now.date()
    if last >= today:
        return False
    missed_a_day = last < today - timedelta(days=1)
    return now.hour >= settings.auto_run_hour or missed_a_day


@dataclass
class Outcome:
    profile_id: str
    name: str
    ran: bool
    result: str
    needs_you: int = 0
    notify: bool = False


class Scheduler:
    def __init__(
        self,
        workspace: Workspace,
        notify: Notify | None = None,
        now: Callable[[], datetime] = local_now,
        check_updates: Callable[[], updates.CheckResult] | None = None,
        update_state: Callable[[], updates.UpdateState] = updates.load,
    ) -> None:
        self.workspace = workspace  # always the real one: the demo looks after itself
        self.notify = notify or (lambda _title, _message: None)
        self.now = now
        # None in tests: only the running app goes online to check for updates.
        self.check_updates = check_updates
        self.update_state = update_state
        self.started = now()
        self._retry_at: dict[str, datetime] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # Running in the background -------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="stealth-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(STARTUP_GRACE.total_seconds())
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # one bad tick must not end daily checks for good
                log.exception("scheduled check failed")
            self._stop.wait(TICK_SECONDS)

    # One look at every profile -------------------------------------------------------------

    def tick(self, *, force: bool = False) -> list[Outcome]:
        """Run the check for every profile that's due (or all of them, with `force`)."""
        now = self.now()
        if not force and now - self.started < STARTUP_GRACE:
            return []
        self._updates(now)
        outcomes = []
        for profile_id in self.workspace.store.list_ids():
            if not force and now < self._retry_at.get(profile_id, now):
                continue
            outcome = self._check(profile_id, now, force=force)
            if outcome.ran and outcome.notify:
                self.notify(f"Stealth: {outcome.name}", outcome.result)
            outcomes.append(outcome)
            self._announce_report(profile_id)
        return outcomes

    def _updates(self, now: datetime) -> None:
        """Once a day, look for a newer app and broker list. Never stops the daily checks."""
        if self.check_updates is None or not updates.due(self.update_state(), now):
            return
        try:
            result = self.check_updates()
        except Exception:
            log.exception("update check failed")
            return
        if result.list_updated:
            self.workspace.forget_brokers()
        if result.new_app:
            self.notify(
                "Stealth update",
                f"Stealth {result.new_app} is out. Open Settings in Stealth to download it.",
            )

    def _announce_report(self, profile_id: str) -> None:
        """Once a month, say that last month's report is ready. Only once, even across restarts."""
        if not self.workspace.lock.acquire(blocking=False):
            return  # busy: the next tick will do it
        try:
            with self.workspace.store.open(profile_id) as profile:
                month = monthly.last_finished(profile)
                if month is None or profile.settings().last_report_month == month.key:
                    return
                profile.save_settings(last_report_month=month.key)
                name = profile.person().display_name
        except Exception:  # a report notice must never stop the daily checks
            log.exception("couldn't check for a monthly report")
            return
        finally:
            self.workspace.lock.release()
        self.notify(
            f"Stealth: {name}",
            f"Your {month.label} report is ready. Open Stealth and go to Reports to read it.",
        )

    def _check(self, profile_id: str, now: datetime, *, force: bool) -> Outcome:
        if not self.workspace.lock.acquire(blocking=False):
            return Outcome(profile_id, "", False, "Busy with something else; trying again soon.")
        try:
            with self.workspace.store.open(profile_id) as profile:
                person = profile.person()
                add_known_values([*person.names, *person.addresses, person.display_name])
                settings = profile.settings()
                if not force and not is_due(settings, now):
                    return Outcome(profile_id, person.display_name, False, "Not due.")
                if profile.mail_account() is None:
                    return Outcome(profile_id, person.display_name, False, "No email connected.")
                try:
                    transport, mailbox = self.workspace.mail(profile)
                    report = cycle.run_cycle(
                        profile,
                        self.workspace.brokers(),
                        transport,
                        mailbox,
                        sleep=self.workspace.sleep,
                        hold_for_approval=True,
                    )
                except MailConnectionError as exc:
                    # Often just no internet yet. Don't use up today's check; try again later.
                    self._retry_at[profile_id] = now + RETRY_AFTER
                    log.info("scheduled check couldn't connect: %s", exc.message)
                    return Outcome(profile_id, person.display_name, False, exc.message)
                except MailAuthError as exc:
                    # Retrying won't help until the person fixes the password: say so once a day.
                    result = f"Couldn't sign in to your email. {exc.hint or exc.message}"
                    profile.save_settings(last_auto_run=now.isoformat(), last_auto_result=result)
                    return Outcome(profile_id, person.display_name, True, result, notify=True)
                except StealthError as exc:
                    result = f"Couldn't finish today's check: {exc.message}"
                    profile.save_settings(last_auto_run=now.isoformat(), last_auto_result=result)
                    return Outcome(profile_id, person.display_name, True, result, notify=True)
                needs = views.nav_counts(views.grouped(views.rows(profile)))["/needs"]
                result = summary_line(report, needs)
                profile.save_settings(last_auto_run=now.isoformat(), last_auto_result=result)
                self._retry_at.pop(profile_id, None)
                inbox = report.inbox
                news = bool(inbox.replies or inbox.resolved or inbox.bounces or report.ico_ready)
                return Outcome(profile_id, person.display_name, True, result, needs, news)
        finally:
            self.workspace.lock.release()


def summary_line(report: cycle.CycleReport, needs_you: int) -> str:
    """One calm sentence for the tray and the Settings page."""
    parts = []
    if report.inbox.resolved:
        settled = [name for name, _meaning in report.inbox.resolved]
        parts.append(f"{len(settled)} repl{'y' if len(settled) == 1 else 'ies'} read and recorded")
    if report.inbox.replies:
        n = len(report.inbox.replies)
        parts.append(f"{n} new repl{'y' if n == 1 else 'ies'} to read")
    if report.inbox.bounces:
        parts.append(f"{len(report.inbox.bounces)} email(s) didn't arrive")
    if report.send.sent:
        parts.append(f"{len(report.send.sent)} approved letter(s) sent")
    if report.complaints:
        parts.append(f"{len(report.complaints)} formal complaint(s) sent")
    if report.ico_ready:
        parts.append(f"{len(report.ico_ready)} ready for the ICO")
    if not parts:
        parts.append("Nothing new")
    line = ", ".join(parts)
    line = line[0].upper() + line[1:]
    if needs_you:
        line += f". {needs_you} things need you" if needs_you > 1 else ". 1 thing needs you"
    return line + "."
