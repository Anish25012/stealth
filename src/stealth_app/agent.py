"""The agent: owns the local server, the tray icon and the daily checks, and opens windows.

It stays in the tray until the person quits. With "Start with Windows" on, it starts at sign-in
with no window, so the daily check happens even if Stealth is never opened.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from typing import Any

from stealth_app import runtime
from stealth_app.scheduler import Scheduler
from stealth_app.server import RunningServer, start_server
from stealth_app.tray import build_icon

log = logging.getLogger("stealth.agent")


def open_window() -> subprocess.Popen[bytes]:
    flags = 0
    if sys.platform == "win32":  # a statement, not a ternary, so mypy narrows on other OSes
        flags = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(runtime.self_command("window"), creationflags=flags)  # noqa: S603


class Agent:
    def __init__(self, server: RunningServer) -> None:
        self.server = server
        self.info = runtime.AgentInfo(port=server.port, token=server.token, pid=os.getpid())
        self._window: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def show(self) -> None:
        with self._lock:
            if self._window is not None and self._window.poll() is None:
                return  # already open; bringing it forward across processes comes later
            self._window = open_window()

    def shutdown(self) -> None:
        with self._lock:
            if self._window is not None and self._window.poll() is None:
                self._window.terminate()
        runtime.remove(self.info)
        self.server.stop()


def run_agent(*, open_on_start: bool = True) -> int:
    existing = runtime.running_agent()
    if existing is not None:
        # Launching again from the Start menu should just bring up a window.
        log.info("agent already running on pid %s; asking it for a window", existing.pid)
        if not runtime.request_show(existing):
            open_window()
        return 0

    holder: list[Agent] = []
    icons: list[Any] = []
    # Stopping the tray loop returns control here, where `finally` shuts everything down. That
    # keeps the shutdown off the server's own thread, which a quit request arrives on.
    server = start_server(on_show=lambda: holder[0].show(), on_quit=lambda: icons[0].stop())
    agent = Agent(server)
    holder.append(agent)
    runtime.write(agent.info)
    log.info("agent started on port %s", agent.server.port)

    def notify(title: str, message: str) -> None:
        if icons:
            try:
                icons[0].notify(message, title)
            except Exception:  # a notification is a nicety; never let it stop a check
                log.exception("tray notification failed")

    scheduler = Scheduler(server.dashboard.real, notify=notify)

    def check_now() -> None:
        threading.Thread(
            target=lambda: _report(scheduler.tick(force=True), notify), daemon=True
        ).start()

    try:
        if open_on_start:
            agent.show()
        icons.append(
            build_icon(
                on_open=agent.show,
                on_quit=lambda: None,
                on_check=check_now,
                status=lambda: _status(server.dashboard.real),
            )
        )
        scheduler.start()
        icons[0].run()
    finally:
        scheduler.stop()
        agent.shutdown()
    return 0


def _report(outcomes: list[Any], notify: Any) -> None:
    """After "Check now" from the tray, always say something, even if nothing changed."""
    for outcome in outcomes:
        if outcome.ran and not outcome.notify:
            notify(f"Stealth: {outcome.name}", outcome.result)
        elif not outcome.ran and outcome.result:
            notify("Stealth", outcome.result)


def _status(workspace: Any) -> str:
    """The tray menu's top line: when Stealth last checked by itself."""
    from datetime import datetime

    try:
        store = workspace.store
        if not store.list_ids():
            return "Not set up yet"
        with store.open() as profile:
            last = profile.settings().last_auto_run
    except Exception:
        return "Daily checks"
    if not last:
        return "Not checked yet"
    when = datetime.fromisoformat(last).astimezone()
    return f"Last checked {when:%a %d %b, %H:%M}"
