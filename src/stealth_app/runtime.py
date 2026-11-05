"""How a second launch finds the agent that's already running.

The agent writes its port and token to `agent.json` in the private data folder. Anyone who can
read that file is already this user, with access to everything Stealth holds.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from stealth.core import paths
from stealth_app.server import TOKEN_HEADER


@dataclass(frozen=True)
class AgentInfo:
    port: int
    token: str
    pid: int

    @property
    def login_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/auth?t={self.token}"


def runtime_file(home: Path | None = None) -> Path:
    return (home or paths.app_home()) / "agent.json"


def write(info: AgentInfo, home: Path | None = None) -> Path:
    path = runtime_file(home)
    paths.ensure_private_dir(path.parent)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(info)), "utf-8")
    os.replace(temp, path)
    return path


def read(home: Path | None = None) -> AgentInfo | None:
    try:
        data = json.loads(runtime_file(home).read_text("utf-8"))
        return AgentInfo(port=int(data["port"]), token=str(data["token"]), pid=int(data["pid"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def remove(info: AgentInfo, home: Path | None = None) -> None:
    """Delete the file, unless a newer agent has already replaced it."""
    current = read(home)
    if current is not None and current.token == info.token:
        runtime_file(home).unlink(missing_ok=True)


def is_alive(info: AgentInfo, timeout: float = 2.0) -> bool:
    """True if a Stealth agent holding this token answers on this port."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{info.port}/health", headers={TOKEN_HEADER: info.token}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
    except (OSError, ValueError):
        return False
    return bool(body.get("app") == "stealth")


def request_show(info: AgentInfo, timeout: float = 5.0) -> bool:
    """Ask the running agent to bring up its window. False if it didn't answer."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{info.port}/show",
        data=b"",
        method="POST",
        headers={TOKEN_HEADER: info.token},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return bool(response.status == 200)
    except OSError:
        return False


def running_agent(home: Path | None = None) -> AgentInfo | None:
    info = read(home)
    return info if info is not None and is_alive(info) else None


def self_command(*args: str) -> list[str]:
    """The command line that runs this app again with `args`, frozen or from source."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "stealth_app", *args]
