"""Errors with messages written for the person using the app, not a developer.

Front ends catch `StealthError` and show `message` and `hint`. Anything else is a bug and is
logged locally, with a friendly screen instead of a traceback.
"""

from __future__ import annotations


class StealthError(Exception):
    """A failure we anticipated and can explain in plain English."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class MailAuthError(StealthError):
    """The mail server rejected the sign-in details."""


class MailConnectionError(StealthError):
    """We could not reach the mail server: usually no internet or a wrong server name."""


class MailSendError(StealthError):
    """The server accepted the connection but refused this message."""


class RegistryError(StealthError):
    """The broker list is missing, corrupt or fails its schema."""


class ProfileError(StealthError):
    """A profile is missing, or the requested action does not apply to it."""


class DatabaseKeyError(StealthError):
    """The database could not be unlocked with the key we have."""


class InvalidTransitionError(StealthError):
    """A case was asked to move to a status its current status cannot reach."""


class TemplateRenderError(StealthError):
    """A letter could not be produced, usually because a detail is missing from the profile."""
