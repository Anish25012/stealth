"""Temporary folders are cleaned up, and a folder that can't be is reported, not ignored."""

import logging
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from stealth.core import paths
from tests.conftest import LEFTOVER_PATTERN, leftover_folders, session_temp


def test_tests_use_their_own_temporary_folder():
    assert Path(tempfile.gettempdir()) == session_temp()
    assert session_temp().name.startswith("stealth-tests-")


def test_the_check_sees_a_leftover_folder():
    folder = Path(tempfile.mkdtemp(prefix="stealth-dashboard-demo-"))
    try:
        assert folder.match(LEFTOVER_PATTERN)
        assert folder in leftover_folders()
    finally:
        assert paths.remove_folder(folder)
    assert folder not in leftover_folders()


def test_read_only_files_are_removed_too(tmp_path):
    folder = tmp_path / "demo"
    (folder / "profiles").mkdir(parents=True)
    locked = folder / "profiles" / "profile.db"
    locked.write_bytes(b"x")
    locked.chmod(stat.S_IREAD)
    assert paths.remove_folder(folder)
    assert not folder.exists()


def test_a_folder_that_cant_be_removed_is_reported(tmp_path, monkeypatch, caplog):
    folder = tmp_path / "stuck"
    folder.mkdir()
    monkeypatch.setattr(shutil, "rmtree", lambda *args, **kwargs: None)
    with caplog.at_level(logging.WARNING, logger="stealth.paths"):
        assert paths.remove_folder(folder) is False
    assert "Couldn't delete the folder" in caplog.text


def test_temp_folder_is_removed_even_after_an_error():
    with pytest.raises(RuntimeError), paths.temp_folder("stealth-demo-") as folder:
        (folder / "data.txt").write_text("made up", encoding="utf-8")
        raise RuntimeError("stop")
    assert not folder.exists()
