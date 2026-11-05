"""What the dashboard works on: the person's real profiles, or a throwaway demo.

The dashboard never reaches for profiles, brokers or mail directly. It asks its workspace, so the
demo can swap in a temporary folder, made-up companies and mail servers on this computer without
the pages knowing the difference.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from stealth.core import clock
from stealth.core.errors import StealthError
from stealth.core.secrets import KeyringStore, MemoryStore
from stealth.devmail.servers import LocalImap, LocalSmtp
from stealth.mail.base import Mailbox, Transport
from stealth.mail.factory import open_mail
from stealth.mail.smtp_imap import ImapMailbox, SmtpTransport
from stealth.profiles.store import Profile, ProfileStore
from stealth.registry.loader import Broker, load_registry

log = logging.getLogger("stealth.app.workspace")


class Workspace(Protocol):
    demo: bool
    store: ProfileStore
    # Held by anything that changes a profile, so a page action can't collide with a cycle.
    lock: threading.Lock

    def brokers(self) -> dict[str, Broker]: ...

    def mail(self, profile: Profile) -> tuple[Transport, Mailbox]: ...

    def sleep(self, seconds: float) -> None: ...

    def close(self) -> None: ...


class RealWorkspace:
    demo = False

    def __init__(self, store: ProfileStore | None = None) -> None:
        self.store = store or ProfileStore(KeyringStore())
        self.lock = threading.Lock()
        self._brokers: dict[str, Broker] | None = None

    def brokers(self) -> dict[str, Broker]:
        if self._brokers is None:
            self._brokers = load_registry()
        return self._brokers

    def mail(self, profile: Profile) -> tuple[Transport, Mailbox]:
        return open_mail(profile)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def close(self) -> None:
        pass


class DemoWorkspace:
    """Made-up data in a temporary folder, with mail servers that only listen on this computer.

    Everything is deleted when the demo ends. Nothing in it can reach a real company: the
    addresses end in `.test`, and the only mail server it knows is its own.
    """

    demo = True

    def __init__(self) -> None:
        from stealth.demo import DASHBOARD_BROKERS, PASSWORD, seed_dashboard_demo

        self._folder = Path(tempfile.mkdtemp(prefix="stealth-dashboard-demo-"))
        self.smtp = LocalSmtp().__enter__()
        self.imap = LocalImap(PASSWORD).__enter__()
        self.lock = threading.Lock()
        self._password = PASSWORD
        self._brokers = DASHBOARD_BROKERS
        self.store = ProfileStore(MemoryStore(), home=self._folder)
        with self.lock:
            seed_dashboard_demo(self.store, self.smtp, self.imap, clock.local_date("Europe/London"))

    def brokers(self) -> dict[str, Broker]:
        return self._brokers

    def mail(self, profile: Profile) -> tuple[Transport, Mailbox]:
        account = profile.mail_account()
        assert account is not None
        return SmtpTransport(account, self._password), ImapMailbox(account, self._password)

    def sleep(self, seconds: float) -> None:
        pass  # nothing real is being sent, so there's no mailbox to protect

    def close(self) -> None:
        self.smtp.__exit__(None, None, None)
        self.imap.__exit__(None, None, None)
        shutil.rmtree(self._folder, ignore_errors=True)


# Background jobs ----------------------------------------------------------------------------


@dataclass
class Job:
    """Work that takes longer than a page should wait, such as a cycle pacing its sends."""

    id: str
    title: str
    state: str = "running"  # running, done or failed
    lines: list[str] = field(default_factory=list)
    error: str | None = None
    hint: str | None = None
    next_url: str = "/"


Work = Callable[[Job], None]


class Jobs:
    """Runs one job at a time on a background thread, holding the workspace lock throughout."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._guard = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def running(self) -> Job | None:
        return next((j for j in self._jobs.values() if j.state == "running"), None)

    def start(self, workspace: Workspace, title: str, work: Work, *, next_url: str = "/") -> Job:
        with self._guard:
            if self.running() is not None or not workspace.lock.acquire(blocking=False):
                raise StealthError(
                    "Stealth is busy with something else right now.",
                    hint="Wait for it to finish, then try again.",
                )
            job = Job(id=uuid.uuid4().hex, title=title, next_url=next_url)
            self._jobs[job.id] = job
        thread = threading.Thread(
            target=self._run, args=(workspace, job, work), name="stealth-job", daemon=True
        )
        thread.start()
        return job

    def _run(self, workspace: Workspace, job: Job, work: Work) -> None:
        try:
            work(job)
            job.state = "done"
        except StealthError as exc:
            job.state, job.error, job.hint = "failed", exc.message, exc.hint
        except Exception:
            log.exception("background job failed")
            job.state = "failed"
            job.error = "Something went wrong inside Stealth."
            job.hint = "Details were saved to Stealth's log. The Help page shows how to send them."
        finally:
            workspace.lock.release()

    def wait(self, job_id: str, timeout: float = 30.0) -> Job:
        """Block until a job finishes. For tests."""
        deadline = time.monotonic() + timeout
        job = self._jobs[job_id]
        while job.state == "running":
            if time.monotonic() > deadline:
                raise TimeoutError(job.title)
            time.sleep(0.02)
        return job
