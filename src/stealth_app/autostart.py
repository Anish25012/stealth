"""Starting with Windows, straight into the tray, so daily checks happen without opening Stealth.

It's the per-user Run key in the registry, the same place Windows' own Settings > Apps > Startup
page lists and can switch off. Only the installed app offers it: from source there's no stable
program to point at. The uninstaller removes the entry.
"""

from __future__ import annotations

import contextlib
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Stealth"


def available() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def command() -> str:
    return f'"{sys.executable}" --background'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE)
    except OSError:
        return False
    return isinstance(value, str) and value == command()


def set_enabled(on: bool) -> None:
    if not available() or sys.platform != "win32":  # the second test narrows for mypy
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if on:
            winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, VALUE)
