"""Where Stealth keeps its files. No other module builds an app-data path from scratch."""

from __future__ import annotations

import contextlib
import functools
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import platformdirs

APP_NAME = "Stealth"
HOME_ENV = "STEALTH_HOME"


def app_home() -> Path:
    """Root data folder: $STEALTH_HOME if set, otherwise the OS per-user app-data folder."""
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).expanduser()
    return platformdirs.user_data_path(APP_NAME, appauthor=False, roaming=False)


def profiles_dir(home: Path | None = None) -> Path:
    return (home or app_home()) / "profiles"


def logs_dir(home: Path | None = None) -> Path:
    return (home or app_home()) / "logs"


def extra_brokers_file(home: Path | None = None) -> Path:
    return (home or app_home()) / "brokers_extra.json"


def ensure_private_dir(path: Path) -> Path:
    """Create a folder only the current user can read.

    On POSIX that is mode 700. On Windows the default app-data folder already has a per-user ACL,
    but $STEALTH_HOME can point anywhere, so inheritance is removed explicitly there.
    """
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        if not existed:
            _restrict_windows_acl(path)
    else:
        path.chmod(stat.S_IRWXU)
    return path


@functools.cache
def _current_user_sid() -> str | None:
    """The signed-in user's SID, such as S-1-5-21-..., or None if Windows won't say.

    A bare user name is ambiguous: when the computer has the same name as the user (a common
    default), icacls resolves "anish" to the computer's own account, and a folder whose
    inheritance has been removed then locks its owner out. A SID names exactly one account.
    """
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],  # noqa: S607
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    # One line: "computer\user","S-1-5-21-..."
    fields = [field.strip().strip('"') for field in result.stdout.strip().split(",")]
    if result.returncode != 0 or not fields or not fields[-1].startswith("S-1-"):
        return None
    return fields[-1]


def _restrict_windows_acl(path: Path) -> None:
    # Best effort: failure must not stop the app, and the parent folder is normally private anyway.
    sid = _current_user_sid()
    if sid is None:
        # Removing inheritance without a grant we're sure of could lock the person out. Keep the
        # inherited (per-user) permissions instead.
        return
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:(OI)(CI)F",
        ],
        check=False,
        capture_output=True,
    )


def remove_folder(path: Path) -> bool:
    """Delete a folder and everything in it. True if it's gone.

    Read-only files (Windows won't delete them) are made writable and tried again. Anything
    still left is logged rather than silently ignored, so a folder that can't be removed is
    noticed instead of piling up in the temp folder.
    """
    import logging
    import shutil

    def retry(func: Callable[[str], object], name: str, _error: object) -> None:
        try:
            os.chmod(name, stat.S_IWRITE)
            func(name)
        except OSError:
            pass  # reported below, once, for the whole folder

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=retry)
    if path.exists():
        logging.getLogger("stealth.paths").warning("Couldn't delete the folder %s", path)
        return False
    return True


@contextlib.contextmanager
def temp_folder(prefix: str) -> Iterator[Path]:
    """A new temporary folder, deleted with `remove_folder` afterwards, even after an error."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        remove_folder(path)
