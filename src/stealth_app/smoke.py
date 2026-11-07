"""`--smoke-test`: prove an installed build works, without touching the person's real data.

Frozen builds fail in ways source checkouts don't: a data file left out, a native library
missing, a plugin found through entry points that PyInstaller didn't see. Each check below covers
one of those. CI runs this against the installed app on every OS.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from stealth import __version__


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True


def _run(name: str, fn: Callable[[], str], *, required: bool = True) -> Check:
    try:
        return Check(name, True, fn(), required)
    except Exception as exc:
        last = traceback.format_exception_only(type(exc), exc)[-1].strip()
        return Check(name, False, last, required)


def check_registry() -> str:
    from stealth.core.jurisdictions import for_country
    from stealth.registry.loader import counts, load_registry

    with tempfile.TemporaryDirectory() as tmp:
        numbers = counts(load_registry(Path(tmp) / "none.json"), for_country("GB"))
    if numbers["default"] == 0:
        raise RuntimeError("no default brokers")
    return f"{numbers['default']} default, {numbers['optional']} optional"


def check_database() -> str:
    from stealth.core.errors import DatabaseKeyError
    from stealth.db.connection import migrate, open_db

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "smoke.db"
        conn = open_db(path, b"k" * 32)
        version = migrate(conn)
        conn.close()
        if b"SQLite format 3" in path.read_bytes()[:16]:
            raise RuntimeError("the database was written unencrypted")
        try:
            open_db(path, b"x" * 32).close()
        except DatabaseKeyError:
            pass
        else:
            raise RuntimeError("a wrong key opened the database")
    return f"encrypted, schema v{version}"


def check_keyring() -> str:
    import keyring
    from keyring.backends import fail

    backend = keyring.get_keyring()
    if isinstance(backend, fail.Keyring):
        raise RuntimeError("no keychain backend was found")
    return type(backend).__module__ + "." + type(backend).__name__


def check_demo() -> str:
    from stealth.demo import run_demo

    result = run_demo(lambda _line: None)
    return f"{len(result.statuses)} cases"


def check_server() -> str:
    from stealth_app.server import TOKEN_HEADER, start_server

    server = start_server()
    try:
        health = urllib.request.Request(
            server.base_url + "/health", headers={TOKEN_HEADER: server.token}
        )
        with urllib.request.urlopen(health, timeout=5) as response:
            if json.loads(response.read()).get("app") != "stealth":
                raise RuntimeError("health check gave the wrong answer")
        for request, expected in (
            (urllib.request.Request(server.base_url + "/"), 401),
            (urllib.request.Request(server.base_url + "/", headers={"Host": "evil.test"}), 421),
        ):
            try:
                urllib.request.urlopen(request, timeout=5)
            except urllib.error.HTTPError as exc:
                if exc.code != expected:
                    raise RuntimeError(f"expected {expected}, got {exc.code}") from exc
            else:
                raise RuntimeError(f"expected {expected}, got 200")
    finally:
        server.stop()
    return f"port {server.port}, guards on"


def check_tray() -> str:
    from stealth_app.tray import build_icon, icon_image

    image = icon_image()
    # Builds the full menu, status line and "Check now" included; not shown: it needs the main loop.
    build_icon(lambda: None, lambda: None, on_check=lambda: None, status=lambda: "Not set up yet")
    return f"icon {image.width}x{image.height}"


def check_search_window() -> str:
    """The scan's browser driver shipped: Playwright's node runtime and its scripts."""
    from playwright._impl._driver import compute_driver_executable

    node, cli = (Path(p) for p in compute_driver_executable())
    missing = [p.name for p in (node, cli) if not p.exists()]
    if missing:
        raise RuntimeError(f"search window driver missing: {', '.join(missing)}")
    return f"driver at {node.parent.name}"


# Reads back what actually rendered, so a blocked stylesheet fails rather than passing unstyled.
PAGE_PROBE = "({title: document.title, font: getComputedStyle(document.body).fontFamily})"


def check_window(timeout: float = 60.0) -> str:
    """Open a real window on the real server and read the page title back out of it."""
    import webview

    from stealth_app.server import start_server

    server = start_server()
    seen: dict[str, object] = {}
    window = webview.create_window("Stealth smoke test", server.login_url(), width=500, height=300)
    assert window is not None

    def probe() -> None:
        try:
            if window.events.loaded.wait(timeout):
                seen.update(window.evaluate_js(PAGE_PROBE))
        finally:
            window.destroy()

    timer = threading.Timer(timeout + 15, lambda: os._exit(3))  # a hung GUI must not hang CI
    timer.daemon = True
    timer.start()
    try:
        webview.start(probe, private_mode=True)
    finally:
        timer.cancel()
        server.stop()
    renderer = getattr(webview, "renderer", "unknown")
    if seen.get("title") != "Stealth":
        raise RuntimeError(f"page didn't load in {renderer} (title {seen.get('title')!r})")
    if "system-ui" not in str(seen.get("font", "")):
        raise RuntimeError(f"stylesheet didn't apply in {renderer} (font {seen.get('font')!r})")
    return f"loaded and styled in {renderer}"


def run_checks(*, with_window: bool) -> list[Check]:
    checks = [
        _run("registry", check_registry),
        _run("database", check_database),
        # Linux runners have no Secret Service; there the app will fall back to a passphrase.
        _run("keyring", check_keyring, required=sys.platform != "linux"),
        _run("demo", check_demo),
        _run("server", check_server),
        _run("tray", check_tray),
        _run("search window", check_search_window),
    ]
    # Last, because some GUI toolkits can't be cleanly restarted in the same process.
    if with_window:
        checks.append(_run("window", check_window))
    return checks


def main(*, with_window: bool, report: Path | None) -> int:
    with tempfile.TemporaryDirectory(prefix="stealth-smoke-", ignore_cleanup_errors=True) as tmp:
        # Anything that reaches for the data folder gets a throwaway one, never the real one.
        os.environ["STEALTH_HOME"] = tmp
        checks = run_checks(with_window=with_window)
    passed = all(c.ok for c in checks if c.required)
    document = {
        "passed": passed,
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.platform(),
        "checks": [asdict(c) for c in checks],
    }
    text = json.dumps(document, indent=2)
    if report is not None:
        report.write_text(text, "utf-8")
    if sys.stdout is not None:  # windowed builds on Windows have no console
        print(text)
    return 0 if passed else 1
