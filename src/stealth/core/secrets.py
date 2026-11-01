"""Secret storage. Passwords, tokens and database keys live here and never in files."""

from __future__ import annotations

from typing import Protocol

import keyring
import keyring.errors

from stealth.core.errors import StealthError

SERVICE = "stealth-privacy"


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringStore:
    """The OS keychain: Credential Manager, macOS Keychain, or Secret Service."""

    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(SERVICE, name)
        except keyring.errors.KeyringError as exc:
            raise _unavailable() from exc

    def set(self, name: str, value: str) -> None:
        try:
            keyring.set_password(SERVICE, name, value)
        except keyring.errors.KeyringError as exc:
            raise _unavailable() from exc

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass  # already gone, which is what the caller wanted
        except keyring.errors.KeyringError as exc:
            raise _unavailable() from exc


class MemoryStore:
    """Secrets that vanish on exit, for `stealth demo` and tests."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


def _unavailable() -> StealthError:
    return StealthError(
        "Stealth couldn't reach your computer's password store.",
        hint="On Linux, check a keyring service such as GNOME Keyring is running.",
    )
