"""The window process: one native window showing the agent's pages, and nothing else."""

from __future__ import annotations

import logging

from stealth_app import runtime

log = logging.getLogger("stealth.window")

TITLE = "Stealth"


def run_window() -> int:
    info = runtime.running_agent()
    if info is None:
        log.warning("window started with no agent running")
        return 1
    import webview  # imported late: it loads a GUI toolkit, which the agent doesn't need

    webview.create_window(TITLE, info.login_url, width=1100, height=760, min_size=(720, 520))
    # Private mode keeps no cookies or cache on disk between launches.
    webview.start(private_mode=True)
    return 0
