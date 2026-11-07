"""Checking for a newer Stealth and a newer broker list, once a day.

Both checks are plain downloads from GitHub: nothing about the person is sent, only the app's
version in the user agent. A new app version is announced, never installed: the installers
aren't code-signed, so the person downloads it themselves from the release page. A new broker
list is installed straight away, but only after its signature is checked (see
`stealth.registry.updater`).

The person can turn checking off in Settings. The state lives in the data folder, not a profile,
because it's about this computer's copy of Stealth.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from stealth import __version__
from stealth.core import clock, paths
from stealth.core.errors import StealthError
from stealth.registry import loader, updater

RELEASES_URL = f"https://api.github.com/repos/{updater.REPO}/releases?per_page=30"
EVERY = timedelta(days=1)
TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass
class UpdateState:
    enabled: bool = True
    checked_at: str | None = None
    result: str = ""
    latest_app: str | None = None  # a newer version than this one, if there is one
    latest_url: str | None = None
    announced: str | None = None  # the newest version the tray has already mentioned

    @property
    def checked(self) -> datetime | None:
        return datetime.fromisoformat(self.checked_at) if self.checked_at else None


def _file(home: Path | None) -> Path:
    return (home or paths.app_home()) / "updates.json"


def load(home: Path | None = None) -> UpdateState:
    try:
        raw = json.loads(_file(home).read_text("utf-8"))
        fields = UpdateState.__dataclass_fields__
        return UpdateState(**{k: v for k, v in raw.items() if k in fields})
    except (OSError, ValueError, TypeError):
        return UpdateState()


def save(state: UpdateState, home: Path | None = None) -> None:
    paths.ensure_private_dir(_file(home).parent)
    _file(home).write_text(json.dumps(asdict(state), indent=1), "utf-8")


def version_tuple(tag: str) -> tuple[int, int, int] | None:
    match = TAG.match(tag.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def newest_release(releases: list[dict[str, object]]) -> tuple[str, str] | None:
    """The highest app release: (version, page URL). Drafts, pre-releases and the list skipped."""
    best: tuple[tuple[int, int, int], str, str] | None = None
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = str(release.get("tag_name", ""))
        parsed = version_tuple(tag)
        if parsed and (best is None or parsed > best[0]):
            best = (parsed, tag.lstrip("v"), str(release.get("html_url", "")))
    return (best[1], best[2]) if best else None


def due(state: UpdateState, now: datetime) -> bool:
    return state.enabled and (state.checked is None or now - state.checked >= EVERY)


@dataclass(frozen=True)
class CheckResult:
    state: UpdateState
    list_updated: bool
    new_app: str | None  # set the first time a newer version is seen, for the tray


def check(
    fetch: updater.Fetch = updater.fetch_bytes,
    home: Path | None = None,
    current: str = __version__,
) -> CheckResult:
    """Run both checks now and record what happened. Raises nothing: failures are recorded."""
    state = load(home)
    state.checked_at = clock.now().isoformat()
    parts: list[str] = []
    list_updated = False
    try:
        releases = json.loads(fetch(RELEASES_URL).decode("utf-8"))
        newest = newest_release(releases if isinstance(releases, list) else [])
        mine = version_tuple(current) or (0, 0, 0)
        if newest and (version_tuple(newest[0]) or (0, 0, 0)) > mine:
            state.latest_app, state.latest_url = newest
            parts.append(f"Stealth {newest[0]} is available")
        else:
            state.latest_app = state.latest_url = None
            parts.append("Stealth is up to date")
        installed = int(loader.current_document(home)["version"])
        outcome = updater.update(installed, fetch, home)
        list_updated = outcome.updated
        parts.append(outcome.message.rstrip(".").lower() if outcome.updated else "")
    except (StealthError, ValueError) as exc:
        message = exc.message if isinstance(exc, StealthError) else "GitHub's answer was garbled"
        parts = [f"Couldn't check: {message.rstrip('.')}"]
    state.result = "; ".join(p for p in parts if p) + "."
    new_app = None
    if state.latest_app and state.announced != state.latest_app:
        new_app = state.announced = state.latest_app
    save(state, home)
    return CheckResult(state, list_updated, new_app)


def set_enabled(enabled: bool, home: Path | None = None) -> None:
    state = load(home)
    state.enabled = enabled
    save(state, home)


def list_version(home: Path | None = None) -> int:
    return int(loader.current_document(home)["version"])
