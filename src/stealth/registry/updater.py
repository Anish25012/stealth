"""Newer broker lists, downloaded and checked before use.

The project publishes `brokers.json` and its signature as assets of the `broker-list` release on
GitHub. A download replaces the list built into the app only if:

- its ed25519 signature matches the key built into the app (see `signing.py`);
- it passes the same schema check as the built-in list;
- its version is higher than the built-in list and every list this computer has accepted before,
  so an old (genuinely signed) list can't be replayed to bring back dead addresses.

The accepted copy is kept in the data folder and checked again every time it's loaded.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stealth import __version__
from stealth.core import paths
from stealth.core.errors import RegistryError, StealthError
from stealth.registry import signing

REPO = "Anish25012/stealth"
LIST_URL = f"https://github.com/{REPO}/releases/download/broker-list/brokers.json"
SIGNATURE_URL = LIST_URL + ".sig"
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 20

Fetch = Callable[[str], bytes]


def fetch_bytes(url: str) -> bytes:
    """One HTTPS GET with a timeout and a size cap. Sends nothing about the person."""
    if not url.startswith("https://"):
        raise StealthError(f"Stealth only downloads over HTTPS: {url}")
    request = urllib.request.Request(  # noqa: S310 - https only
        url, headers={"User-Agent": f"Stealth/{__version__} (+https://github.com/{REPO})"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            body: bytes = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise StealthError(f"GitHub answered {exc.code} for {url}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StealthError(
            "Couldn't reach GitHub to check for updates.",
            hint=f"Check your internet connection. ({getattr(exc, 'reason', exc)})",
        ) from exc
    if len(body) > MAX_BYTES:
        raise StealthError("The download was far bigger than expected, so Stealth ignored it.")
    return body


def folder(home: Path | None = None) -> Path:
    return (home or paths.app_home()) / "registry"


def _highest_file(home: Path | None) -> Path:
    return folder(home) / "highest_version.json"


def highest_seen(home: Path | None = None) -> int:
    try:
        return int(json.loads(_highest_file(home).read_text("utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def read_signed(data: bytes, signature: bytes, origin: str) -> dict[str, Any]:
    """Parse and validate a list, refusing it unless the signature is good."""
    from stealth.registry.loader import validate  # the loader imports this module

    if not signing.verify(data, signature):
        raise RegistryError(f"The broker list from {origin} isn't signed by the Stealth project.")
    try:
        document: dict[str, Any] = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError(f"The broker list from {origin} isn't valid JSON.") from exc
    validate(document, origin)
    return document


def cached_document(home: Path | None = None) -> dict[str, Any] | None:
    """The downloaded list, if there is one and it still checks out. Never raises."""
    where = folder(home)
    try:
        data = (where / "brokers.json").read_bytes()
        signature = (where / "brokers.json.sig").read_bytes()
        return read_signed(data, signature, "the downloaded copy")
    except (OSError, RegistryError):
        return None


@dataclass(frozen=True)
class ListUpdate:
    updated: bool
    version: int
    message: str


def update(installed: int, fetch: Fetch = fetch_bytes, home: Path | None = None) -> ListUpdate:
    """Download the published list and keep it if it's genuine and newer than `installed`."""
    data = fetch(LIST_URL)
    signature = fetch(SIGNATURE_URL)
    document = read_signed(data, signature, "GitHub")
    version = int(document["version"])
    floor = max(installed, highest_seen(home))
    if version <= floor:
        return ListUpdate(False, installed, "The broker list is up to date.")
    where = paths.ensure_private_dir(folder(home))
    # Signature first, list second: a half-finished write fails the check and is ignored.
    (where / "brokers.json.sig").write_bytes(signature)
    (where / "brokers.json").write_bytes(data)
    _highest_file(home).write_text(json.dumps({"version": version}), "utf-8")
    added = len(document["brokers"])
    return ListUpdate(True, version, f"Downloaded broker list {version} ({added} entries).")
