import getpass
import logging
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stealth.core import clock, logging_scrub, paths
from stealth.core.secrets import MemoryStore


def test_clock_freezes_and_advances():
    with clock.frozen_at(datetime(2026, 1, 31, 12, tzinfo=UTC)):
        assert clock.now() == datetime(2026, 1, 31, 12, tzinfo=UTC)
        clock.advance(timedelta(days=1))
        assert clock.now().date() == date(2026, 2, 1)


def test_clock_rejects_naive_datetime():
    with pytest.raises(ValueError):
        clock.set_time(datetime(2026, 1, 1))


def test_frozen_at_restores_real_time():
    with clock.frozen_at(datetime(2000, 1, 1, tzinfo=UTC)):
        pass
    assert clock.now().year > 2000


def test_local_date_uses_the_persons_time_zone():
    # 23:30 UTC on 31 May is 00:30 on 1 June during British Summer Time.
    with clock.frozen_at(datetime(2026, 5, 31, 23, 30, tzinfo=UTC)):
        assert clock.local_date("Europe/London") == date(2026, 6, 1)
        assert clock.local_date("UTC") == date(2026, 5, 31)


def test_app_home_honours_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    assert paths.app_home() == tmp_path
    assert paths.profiles_dir() == tmp_path / "profiles"
    assert paths.logs_dir() == tmp_path / "logs"
    assert paths.extra_brokers_file() == tmp_path / "brokers_extra.json"


def test_app_home_defaults_to_platform_folder(monkeypatch):
    monkeypatch.delenv(paths.HOME_ENV, raising=False)
    assert "Stealth" in str(paths.app_home())


def test_ensure_private_dir_creates_nested_folder(tmp_path):
    target = tmp_path / "a" / "b"
    assert paths.ensure_private_dir(target).is_dir()
    assert paths.ensure_private_dir(target).is_dir()  # idempotent


def _nested_private_dirs_are_writable(root: Path) -> None:
    # The same chain a new profile makes: home, then profiles, then the profile's own folder.
    deepest = root / "home" / "profiles" / "0123456789ab"
    for folder in (root / "home", root / "home" / "profiles", deepest):
        paths.ensure_private_dir(folder)
    (deepest / "evidence").mkdir()
    (deepest / "evidence" / "note.txt").write_text("still mine", encoding="utf-8")
    assert (deepest / "evidence" / "note.txt").read_text(encoding="utf-8") == "still mine"


@pytest.fixture
def fresh_sid_lookup():
    paths._current_user_sid.cache_clear()
    yield
    paths._current_user_sid.cache_clear()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")
def test_nested_private_folders_stay_writable_on_windows(tmp_path, fresh_sid_lookup):
    _nested_private_dirs_are_writable(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")
def test_a_computer_named_like_its_user_doesnt_lock_them_out(
    tmp_path, monkeypatch, fresh_sid_lookup
):
    # On a PC called "anish" with a user called "anish", the bare name means the computer's own
    # account to icacls. Make the user name look like the computer name and check that it
    # doesn't matter.
    computer = os.environ["COMPUTERNAME"]
    monkeypatch.setenv("USERNAME", computer)
    monkeypatch.setattr(getpass, "getuser", lambda: computer)
    _nested_private_dirs_are_writable(tmp_path)


def test_the_folder_is_granted_to_the_users_sid_not_their_name(
    tmp_path, monkeypatch, fresh_sid_lookup
):
    sid = "S-1-5-21-1111111111-2222222222-3333333333-1001"
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        out = f'"anish\\anish","{sid}"\r\n' if args[0] == "whoami" else ""
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setattr(paths.subprocess, "run", fake_run)
    monkeypatch.setattr(getpass, "getuser", lambda: "anish")
    paths.ensure_private_dir(tmp_path / "one")
    paths.ensure_private_dir(tmp_path / "two")

    icacls = [call for call in calls if call[0] == "icacls"]
    assert [call[-1] for call in icacls] == [f"*{sid}:(OI)(CI)F"] * 2
    assert not any("anish:" in arg for call in icacls for arg in call)
    assert sum(call[0] == "whoami" for call in calls) == 1  # looked up once, then remembered


@pytest.mark.parametrize(
    "whoami",
    [
        subprocess.CompletedProcess(["whoami"], 1, stdout="", stderr="no"),
        subprocess.CompletedProcess(["whoami"], 0, stdout="garbage\r\n", stderr=""),
        OSError("whoami isn't there"),
    ],
)
def test_without_a_sid_inheritance_is_left_alone(tmp_path, monkeypatch, fresh_sid_lookup, whoami):
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if isinstance(whoami, Exception):
            raise whoami
        return whoami

    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setattr(paths.subprocess, "run", fake_run)
    assert paths.ensure_private_dir(tmp_path / "kept").is_dir()
    assert [call[0] for call in calls] == ["whoami"]  # no icacls /inheritance:r


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_private_dir_is_mode_700(tmp_path):
    target = paths.ensure_private_dir(tmp_path / "private")
    assert target.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    ("raw", "leak"),
    [
        ("sent to jane.doe+x@example.co.uk ok", "jane.doe"),
        ("call 07700 900123 now", "900123"),
        ("mobile +44 7700 900123", "900123"),
        ("lives at SW1A 1AA today", "SW1A"),
        ("lives at m1 1ae today", "m1 1ae"),
    ],
)
def test_scrub_removes_personal_data(raw, leak):
    assert leak not in logging_scrub.scrub(raw)


def test_scrub_leaves_ordinary_log_text_alone():
    line = "2026-11-01 21:15:51 case 1234567890 moved to sent after 20 seconds"
    assert logging_scrub.scrub(line) == line


def test_scrub_known_values_ignores_case():
    logging_scrub.add_known_values(["Jane Doe", "ab"])  # too-short values are ignored
    try:
        assert logging_scrub.scrub("hello JANE DOE, grab it") == "hello [personal], grab it"
    finally:
        logging_scrub.clear_known_values()


def test_log_file_never_contains_personal_data(tmp_path: Path):
    path = logging_scrub.setup_logging(tmp_path)
    log = logging.getLogger("stealth.test")
    log.info("sending to %s", "secret.person@example.com")
    try:
        raise RuntimeError("failed for secret.person@example.com")
    except RuntimeError:
        log.exception("boom")
    for handler in logging.getLogger("stealth").handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8")
    assert "secret.person" not in text
    assert "[email]" in text
    assert "RuntimeError" in text


def test_memory_store_roundtrip():
    store = MemoryStore()
    store.set("a", "1")
    assert store.get("a") == "1"
    store.delete("a")
    store.delete("a")
    assert store.get("a") is None
