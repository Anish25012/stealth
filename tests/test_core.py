import logging
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
