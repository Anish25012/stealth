"""The search window: a real browser the person searches in, and Stealth photographs.

People-search sites forbid automated searching (ukphonebook's robots.txt blocks it, for one), so
Stealth doesn't search for anyone. It opens Edge or Chrome, already installed on the computer, at
the site; the person types their own name, as they could in any browser; and when the results are
on screen, Stealth captures the page: a screenshot, its address and its text, kept as evidence.

The window uses its own throwaway browser profile, so it never sees the person's own browsing,
cookies or saved passwords. Playwright's sync API must stay on the thread that started it, so
every call runs on one worker thread.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stealth.core.errors import StealthError

log = logging.getLogger("stealth.scan")

CHANNELS = ("msedge", "chrome")  # what's already installed, so nothing big is downloaded
WAIT = 90  # seconds: starting a browser for the first time can be slow


@dataclass(frozen=True)
class Capture:
    url: str
    title: str
    png: bytes
    text: str


class NoBrowser(StealthError):
    pass


class SearchWindow:
    def __init__(self, *, headless: bool = False) -> None:
        self.headless = headless
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stealth-search")
        self._playwright: Any = None
        self._context: Any = None
        self._folder: Path | None = None

    # Called from any thread ----------------------------------------------------------------

    def open(self, url: str) -> None:
        try:
            self._worker.submit(self._open, url).result(timeout=WAIT)
        except StealthError:
            raise
        except Exception as exc:  # the browser stalled or closed mid-way
            log.warning("search window didn't open: %s", exc)
            raise StealthError(
                "The search window didn't open properly.",
                hint="Press Open again. If it keeps happening, close any open search window first.",
            ) from exc

    def capture(self) -> Capture:
        try:
            result: Capture = self._worker.submit(self._capture).result(timeout=WAIT)
        except StealthError:
            raise
        except Exception as exc:  # the page froze or the window was closed mid-capture
            log.warning("search window capture failed: %s", exc)
            raise StealthError(
                "Stealth couldn't capture the search window.",
                hint="Check the results are showing, then press Capture again.",
            ) from exc
        return result

    @property
    def is_open(self) -> bool:
        return self._worker.submit(self._alive).result(timeout=WAIT)

    def close(self) -> None:
        try:
            self._worker.submit(self._close).result(timeout=WAIT)
        finally:
            self._worker.shutdown(wait=False)

    # On the worker thread ------------------------------------------------------------------

    def _alive(self) -> bool:
        return self._context is not None and bool(self._context.pages)

    def _start(self) -> None:
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError as exc:  # pragma: no cover - the app always ships it
            raise NoBrowser("The search window isn't part of this copy of Stealth.") from exc
        for attempt in range(3):
            if self._playwright is not None:
                break
            # The driver process occasionally dies while starting on Windows: try again.
            try:
                self._playwright = sync_playwright().start()
            except Exception:
                if attempt == 2:
                    raise
        self._folder = Path(tempfile.mkdtemp(prefix="stealth-search-"))
        # chromium_sandbox: Playwright turns the browser's sandbox off by default, and Edge then
        # shows a "--no-sandbox" security warning. These pages are untrusted: keep it on.
        options: dict[str, Any] = {
            "headless": self.headless,
            "no_viewport": not self.headless,
            "chromium_sandbox": True,
        }
        for channel in CHANNELS:
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    str(self._folder), channel=channel, **options
                )
            except Error:
                continue
            self._context.on("close", lambda _ctx: self._forget())
            return
        raise NoBrowser(
            "Stealth couldn't open a search window.",
            hint="It uses Microsoft Edge or Google Chrome. Install either one, then try again.",
        )

    def _forget(self) -> None:
        self._context = None

    def _open(self, url: str) -> None:
        if not self._alive():
            self._close_context()
            self._start()
        pages = self._context.pages
        page = pages[0] if pages else self._context.new_page()
        # A freshly started browser sometimes aborts or stalls its first navigation: try again.
        # "commit" is enough: the window is open once the page starts arriving.
        for attempt in range(3):
            try:
                page.goto(url, wait_until="commit", timeout=20_000)
                break
            except Exception:
                if attempt == 2:
                    raise
                log.debug("search window navigation failed, retrying", exc_info=True)
        page.bring_to_front()
        if not self.headless:
            self._raise_window(page)

    def _raise_window(self, page: Any) -> None:
        """Put the window in front of everything. Stealth runs in the background, and Windows
        won't let a background program push a window forward, so the window minimises and
        restores itself, which Windows does allow."""
        try:
            cdp = self._context.new_cdp_session(page)
            window = cdp.send("Browser.getWindowForTarget")["windowId"]
            for state in ("minimized", "normal"):
                cdp.send(
                    "Browser.setWindowBounds",
                    {"windowId": window, "bounds": {"windowState": state}},
                )
            cdp.detach()
        except Exception as exc:  # a nicety: the window is open either way
            log.debug("couldn't bring the search window forward: %s", exc)

    def _capture(self) -> Capture:
        if not self._alive():
            raise StealthError(
                "The search window isn't open.",
                hint="Open it again from the Scan page, search, then capture.",
            )
        page = self._context.pages[-1]  # the newest tab: sites often open results in one
        # Ask the page itself (Playwright's load-state wait can miss an event that fired early),
        # and if it's still loading, photograph what's there: the person can see it already.
        try:
            page.wait_for_function("document.readyState !== 'loading'", polling=250, timeout=10_000)
        except Exception:
            log.debug("captured a page that was still loading", exc_info=True)
        png = page.screenshot(full_page=True, timeout=30_000)
        text = page.inner_text("body", timeout=10_000)
        return Capture(url=page.url, title=page.title(), png=png, text=text[:50_000])

    def _close_context(self) -> None:
        if self._context is not None:
            with contextlib.suppress(Exception):  # already gone is fine
                self._context.close()
            self._context = None
        if self._folder is not None:
            shutil.rmtree(self._folder, ignore_errors=True)
            self._folder = None

    def _close(self) -> None:
        self._close_context()
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
