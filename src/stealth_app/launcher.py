"""Entry point for the desktop app. One executable, several roles chosen by arguments."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from stealth import __version__

log = logging.getLogger("stealth.app")


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="Stealth", description="Stealth desktop app.")
    parser.add_argument("role", nargs="?", choices=["agent", "window"], default="agent")
    parser.add_argument("--background", action="store_true", help="Start in the tray only.")
    parser.add_argument("--smoke-test", action="store_true", help="Check this build, then exit.")
    parser.add_argument("--with-window", action="store_true", help="Smoke test opens a window.")
    parser.add_argument("--report", type=Path, help="Write the smoke-test result here.")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.smoke_test:
        from stealth_app import smoke

        return smoke.main(with_window=args.with_window, report=args.report)

    from stealth.core import paths
    from stealth.core.logging_scrub import setup_logging

    setup_logging(paths.logs_dir())
    try:
        if args.role == "window":
            from stealth_app.window import run_window

            return run_window()
        from stealth_app.agent import run_agent

        return run_agent(open_on_start=not args.background)
    except Exception:
        log.exception("the app stopped unexpectedly")
        return 2
