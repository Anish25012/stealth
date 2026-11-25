import logging
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from stealth.core import clock
from stealth.core.paths import remove_folder
from stealth.core.secrets import MemoryStore
from stealth.profiles.models import MailAccount, Person
from stealth.profiles.store import Profile, ProfileStore

# Temporary folders --------------------------------------------------------------------------
#
# Every temporary folder made during the tests, by this process or a subprocess, goes in one
# private folder for the session. After each test, a Stealth folder left in there fails that
# test, so a demo, scan or smoke check that doesn't clean up is caught where it happens. Copies
# of Stealth running elsewhere on the computer can't confuse the check.

LEFTOVER_PATTERN = "stealth-*"
_session_temp: Path | None = None
_original_temp: tuple[str | None, dict[str, str | None]] = (None, {})


def pytest_configure(config: pytest.Config) -> None:
    global _session_temp, _original_temp
    _session_temp = Path(tempfile.mkdtemp(prefix="stealth-tests-"))
    names = ("TMP", "TEMP", "TMPDIR")
    _original_temp = (tempfile.tempdir, {n: os.environ.get(n) for n in names})
    for name in names:
        os.environ[name] = str(_session_temp)
    tempfile.tempdir = str(_session_temp)


def pytest_unconfigure(config: pytest.Config) -> None:
    tempdir, env = _original_temp
    tempfile.tempdir = tempdir
    for name, value in env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    if _session_temp is not None and not remove_folder(_session_temp):
        message = f"Warning: couldn't delete the tests' temporary folder {_session_temp}"
        print(message, file=sys.stderr)


def session_temp() -> Path:
    assert _session_temp is not None
    return _session_temp


def leftover_folders() -> set[Path]:
    return {p for p in session_temp().glob(LEFTOVER_PATTERN) if p.is_dir()}


@pytest.fixture(autouse=True)
def _no_temporary_folders_left_behind() -> Iterator[None]:
    before = leftover_folders()
    yield
    left = leftover_folders() - before
    for folder in left:
        remove_folder(folder)  # so one leak doesn't fail every test after it
    if left:
        names = ", ".join(sorted(p.name for p in left))
        pytest.fail(f"This test left temporary folders behind: {names}", pytrace=False)


@pytest.fixture(autouse=True)
def _logging_put_back() -> Iterator[None]:
    """Close any log file a test opened (the CLI and app do), and restore the logger.

    An open log file can't be deleted on Windows, and a handler left behind would also catch
    the next test's logging.
    """
    logger = logging.getLogger("stealth")
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    yield
    for handler in logger.handlers:
        if handler not in handlers:
            handler.close()
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


@pytest.fixture(autouse=True)
def _real_clock_between_tests() -> Iterator[None]:
    yield
    clock.reset()


@pytest.fixture(autouse=True)
def _never_the_real_data_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything that falls back to the default data folder gets a throwaway one."""
    monkeypatch.setenv("STEALTH_HOME", str(tmp_path / "default-home"))


@pytest.fixture(autouse=True)
def _no_google_client_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google sign-in is off in tests, so nothing reaches Google; tests that want it fake it."""
    from stealth.mail import google_oauth

    monkeypatch.setattr(google_oauth, "CLIENT_ID", "")
    monkeypatch.setattr(google_oauth, "built_in_secret", lambda: "")
    monkeypatch.delenv("STEALTH_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("STEALTH_GOOGLE_CLIENT_SECRET", raising=False)


@pytest.fixture(autouse=True)
def _never_have_i_been_pwned(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches Have I Been Pwned; tests that check HIBP give the provider a fake opener."""
    from stealth.breaches import hibp

    def refuse(_request: object) -> tuple[int, dict[str, str], bytes]:
        raise AssertionError("a test tried to reach Have I Been Pwned")

    monkeypatch.setattr(hibp, "_urlopen", refuse)


@pytest.fixture(autouse=True)
def _never_a_real_confirmation_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test opens a company's real website; tests that follow links pass a fake `get`."""
    from stealth.inbox import verify

    def refuse(_url: str, _domains: set[str]) -> object:
        raise AssertionError("a test tried to open a real confirmation link")

    monkeypatch.setattr(verify, "http_get", refuse)


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    return ProfileStore(MemoryStore(), home=tmp_path / "home")


@pytest.fixture
def person() -> Person:
    return Person(
        display_name="Test Person",
        names=["Test Person", "T. Person"],
        emails=["test.person@example.org", "old.address@example.org"],
        addresses=["1 Example Road, London, SW1A 1AA"],
        phones=["07700 900123"],
    )


@pytest.fixture
def profile(store: ProfileStore, person: Person) -> Iterator[Profile]:
    profile_id, _ = store.create()
    with store.open(profile_id) as opened:
        opened.save_person(person)
        opened.save_mail_account(
            MailAccount(
                provider="smtp",
                address="test.person@example.org",
                smtp_host="127.0.0.1",
                smtp_port=1,
                smtp_security="none",
                imap_host="127.0.0.1",
                imap_port=1,
                imap_security="none",
                username="test.person@example.org",
            ),
            password="pw",
        )
        yield opened
