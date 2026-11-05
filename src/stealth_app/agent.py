"""The agent: owns the local server and the tray icon, and opens windows on request.

Scheduling and catch-up arrive in Phase 2.7. For now the agent serves pages and stays in the tray
until the person quits.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

from stealth_app import runtime
from stealth_app.server import RunningServer, start_server
from stealth_app.tray import build_icon

log = logging.getLogger("stealth.agent")


def open_window() -> subprocess.Popen[bytes]:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
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
    agent = Agent(start_server(on_show=lambda: holder[0].show()))
    holder.append(agent)
    runtime.write(agent.info)
    log.info("agent started on port %s", agent.server.port)
    try:
        if open_on_start:
            agent.show()
        build_icon(on_open=agent.show, on_quit=agent.shutdown).run()
    finally:
        agent.shutdown()
    return 0
