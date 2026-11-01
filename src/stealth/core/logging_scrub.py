"""Local logging that cannot leak personal data.

Logs may be pasted to a helper by a family member, so they are scrubbed at the point of writing
rather than on export. Patterns catch the shapes of personal data; `add_known_values` catches
things with no shape, such as names.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from stealth.core.paths import ensure_private_dir

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Requires a leading 0 or + so timestamps and case numbers are not treated as phone numbers.
PHONE_RE = re.compile(r"(?<![\w.:-])(?:\+\d{1,3}[ \-]?\d|0\d)[\d \-]{8,12}\d\b")
UK_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b", re.IGNORECASE)

_known_values: set[str] = set()


def add_known_values(values: list[str]) -> None:
    """Register profile values (names, street addresses) to redact wherever they appear."""
    _known_values.update(v for v in values if len(v) >= 3)


def clear_known_values() -> None:
    _known_values.clear()


def scrub(text: str) -> str:
    """Replace personal data in `text` with placeholders."""
    for value in sorted(_known_values, key=len, reverse=True):
        text = re.sub(re.escape(value), "[personal]", text, flags=re.IGNORECASE)
    text = EMAIL_RE.sub("[email]", text)
    text = UK_POSTCODE_RE.sub("[postcode]", text)
    return PHONE_RE.sub("[phone]", text)


class ScrubFilter(logging.Filter):
    """Rewrites each record's final message, including any exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = scrub(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(directory: Path) -> Path:
    """Send scrubbed logs to a rotating file in `directory` and return the file's path."""
    ensure_private_dir(directory)
    path = directory / "stealth.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(ScrubFilter())
    root = logging.getLogger("stealth")
    for old in root.handlers:
        old.close()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False
    return path
