"""Screenshot every dashboard page in light and dark, from the demo, for the README and reviews.

    pip install playwright        # uses the Edge or Chrome already installed; no download
    python tools/screenshots.py                     # all pages into docs/screenshots
    python tools/screenshots.py --only overview,styleguide --out some/folder

It runs its own server on a throwaway data folder, so a real profile is never opened. Any console
error, failed request or Content-Security-Policy violation is reported, and makes it exit 1.
The same `capture` function backs the visual smoke test in tests/test_visual.py.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (file name, path). The welcome page and the setup steps are taken first, before the demo starts;
# setup is walked through for real, against local mail servers on this computer. "message" and
# "job" are reached by following the app, not a fixed URL; "job" runs a cycle in the demo, so it
# goes last. "error" is a deliberate 400.
SETUP = [
    "setup",
    "setup-details",
    "setup-email",
    "setup-recovery",
    "setup-companies",
    "setup-finish",
]
BEFORE_DEMO = {"welcome", *SETUP}
PAGES = [
    ("welcome", "/"),
    ("setup", "/setup"),
    ("setup-details", "/setup/details"),
    ("setup-email", "/setup/email"),
    ("setup-recovery", "/setup/recovery"),
    ("setup-companies", "/setup/companies"),
    ("setup-finish", "/setup/finish"),
    ("overview", "/"),
    ("companies", "/cases"),
    ("company", "/cases/chatty-data"),
    ("company-refused", "/cases/refusing-corp"),
    ("message", "/cases/chatty-data"),
    ("needs", "/needs"),
    ("approve", "/approve"),
    ("reports", "/reports"),
    ("settings", "/settings"),
    ("people", "/people"),
    ("help", "/help"),
    ("error", "/cases/no-such-company"),
    ("styleguide", "/styleguide"),
    ("job", "/"),
]
EXPECTED_STATUS = {"error": 400}
MAIL_PASSWORD = "local-only"  # noqa: S105 - the throwaway local test servers' password


def pages() -> list[tuple[str, str]]:
    """PAGES, less the setup screens and People when the setup wizard isn't part of the app."""
    from importlib.util import find_spec

    if find_spec("stealth_app.setup_wizard") is not None:
        return list(PAGES)
    return [p for p in PAGES if p[0] not in {*SETUP, "people"}]


CHANNELS = ("msedge", "chrome", None)  # None: Playwright's own Chromium, if it's installed


@dataclass
class Shot:
    name: str
    theme: str
    path: str
    file: Path | None
    problems: list[str] = field(default_factory=list)
    background: str = ""  # the page's computed background colour, to prove the theme applied


@contextmanager
def demo_server() -> Iterator[object]:
    """A server with the style guide on, over a throwaway data folder. Restores the environment."""
    saved = {key: os.environ.get(key) for key in ("STEALTH_DEV", "STEALTH_HOME")}
    with tempfile.TemporaryDirectory(prefix="stealth-shots-") as tmp:
        os.environ["STEALTH_DEV"] = "1"
        os.environ["STEALTH_HOME"] = str(Path(tmp) / "default-home")
        from stealth.core.secrets import MemoryStore
        from stealth.profiles.store import ProfileStore
        from stealth_app.server import start_server
        from stealth_app.workspace import RealWorkspace

        server = start_server(
            workspace=RealWorkspace(ProfileStore(MemoryStore(), Path(tmp) / "home"))
        )
        try:
            yield server
        finally:
            server.stop()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _browser(playwright: object) -> object:
    errors = []
    for channel in CHANNELS:
        try:
            options = {"channel": channel} if channel else {}
            return playwright.chromium.launch(**options)  # type: ignore[attr-defined]
        except Exception as exc:
            errors.append(f"{channel or 'chromium'}: {str(exc).splitlines()[0]}")
    raise RuntimeError("No browser for screenshots. " + "; ".join(errors))


def capture(
    out: Path | None,
    only: Iterable[str] = (),
    themes: Iterable[str] = ("light", "dark"),
    width: int = 1280,
    height: int = 860,
    scale: float = 1,
) -> list[Shot]:
    """Load each page in each theme. Saves PNGs to `out` unless it's None."""
    from playwright.sync_api import sync_playwright

    from stealth.devmail.servers import LocalImap, LocalSmtp

    wanted = set(only)
    chosen = [p for p in pages() if not wanted or p[0] in wanted]
    shots: list[Shot] = []
    with sync_playwright() as playwright:
        browser = _browser(playwright)
        for theme in themes:
            # A fresh server each theme: setup makes a profile, and welcome needs there to be none.
            with (
                demo_server() as server,
                LocalSmtp(password=MAIL_PASSWORD) as smtp,
                LocalImap(MAIL_PASSWORD) as imap,
            ):
                ports = (smtp.port, imap.port)
                shots += _shoot_theme(
                    browser, server, theme, chosen, out, ports, (width, height), scale
                )
        browser.close()  # type: ignore[attr-defined]
    return shots


def _advance_setup(
    page: object, server: object, target: str, done: list[str], ports: tuple[int, int]
) -> None:
    """Fill in the setup steps before `target`, so it shows as a person would see it."""
    base, csrf = server.base_url, server.dashboard.csrf  # type: ignore[attr-defined]
    smtp_port, imap_port = ports
    steps = {
        "setup-details": ("/setup/who", {"for": "me"}),
        "setup-email": (
            "/setup/details",
            {
                "name": "Sam Sample",
                "emails": "sam@example.org",
                "addresses": "2 Test Street, Leeds, LS1 1AA",
            },
        ),
        "setup-recovery": (
            "/setup/email",
            {
                "address": "sam@example.org",
                "provider": "other",
                "password": MAIL_PASSWORD,
                "smtp_host": "127.0.0.1",
                "smtp_port": str(smtp_port),
                "smtp_security": "none",
                "imap_host": "127.0.0.1",
                "imap_port": str(imap_port),
                "imap_security": "none",
            },
        ),
        "setup-companies": ("/setup/recovery", {"kept": "yes"}),
    }
    for name in SETUP[1 : SETUP.index(target) + 1]:
        if name in done:
            continue
        if name == "setup-finish":
            page.goto(base + "/setup/companies")  # type: ignore[attr-defined]
            page.get_by_role("button", name="Continue").click()  # type: ignore[attr-defined]
        else:
            url, form = steps[name]
            page.request.post(base + url, form={"csrf": csrf, **form})  # type: ignore[attr-defined]
        done.append(name)


def _shoot_theme(
    browser: object,
    server: object,
    theme: str,
    chosen: list[tuple[str, str]],
    out: Path | None,
    ports: tuple[int, int],
    viewport: tuple[int, int],
    scale: float = 1,
) -> list[Shot]:
    shots: list[Shot] = []
    context = browser.new_context(  # type: ignore[attr-defined]
        viewport={"width": viewport[0], "height": viewport[1]},
        device_scale_factor=scale,
        color_scheme=theme,
        reduced_motion="reduce",  # the ring shows its final value, not mid-animation
    )
    page = context.new_page()
    problems: list[str] = []
    page.on(
        "console",
        lambda m, p=problems: (
            m.type in ("error", "warning") and p.append(f"console {m.type}: {m.text}")
        ),
    )
    page.on("pageerror", lambda e, p=problems: p.append(f"script error: {e}"))
    page.on("requestfailed", lambda r, p=problems: p.append(f"request failed: {r.url}"))
    page.on(
        "response",
        lambda r, p=problems: r.status >= 400 and p.append(f"HTTP {r.status}: {r.url}"),
    )
    # Browsers ask for /favicon.ico on their own; the app window never shows one.
    page.route("**/favicon.ico", lambda route: route.fulfill(status=204))
    page.goto(server.login_url())  # type: ignore[attr-defined]
    base, csrf = server.base_url, server.dashboard.csrf  # type: ignore[attr-defined]
    demo_started = False
    setup_done: list[str] = []
    for name, path in chosen:
        if name not in BEFORE_DEMO and not demo_started:
            page.request.post(f"{base}/demo/start", form={"csrf": csrf})
            demo_started = True
        if name in SETUP:
            _advance_setup(page, server, name, setup_done, ports)
        problems.clear()
        page.goto(base + path)
        if name == "message":
            page.locator('a[href*="/messages/"]').first.click()
        elif name == "job":
            page.get_by_role("button", name="Run cycle now").click()
            # The job page refreshes itself until the cycle is done; shoot the result.
            page.get_by_role("link", name="Continue").wait_for(timeout=30_000)
        page.wait_for_load_state("networkidle")
        expected = EXPECTED_STATUS.get(name)
        if expected:  # the browser reports the page's own status as a failed load
            problems[:] = [p for p in problems if f"{expected}" not in p]
        file = None
        if out is not None:
            out.mkdir(parents=True, exist_ok=True)
            file = out / f"{name}-{theme}.png"
            page.screenshot(path=str(file), full_page=True)
        background = page.evaluate("getComputedStyle(document.body).backgroundColor")
        shots.append(Shot(name, theme, page.url, file, list(problems), background))
    if demo_started:
        page.request.post(f"{base}/demo/stop", form={"csrf": csrf})
    context.close()
    return shots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "screenshots")
    parser.add_argument("--only", default="", help="comma-separated page names")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=860)
    parser.add_argument("--scale", type=float, default=1)
    args = parser.parse_args()
    only = [n for n in args.only.split(",") if n]
    shots = capture(args.out, only, width=args.width, height=args.height, scale=args.scale)
    failed = False
    for shot in shots:
        print(f"{'FAIL' if shot.problems else 'ok  '} {shot.name}-{shot.theme}  {shot.file}")
        for problem in shot.problems:
            print(f"       {problem}")
        failed = failed or bool(shot.problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
