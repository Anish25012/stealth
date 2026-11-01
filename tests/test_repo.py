"""Guards on what the public repo can contain.

An unanchored `profiles/` in .gitignore once kept a whole source package out of Git. Everything
still passed locally, because the files were on disk. These tests fail on any machine.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout"
)


def ignored(paths: list[str]) -> set[str]:
    """The subset of `paths` that .gitignore would exclude."""
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=ROOT,
        input="\n".join(paths),
        capture_output=True,
        text=True,
        check=False,
    )
    return set(result.stdout.split("\n")) - {""}


def test_no_source_file_is_ignored():
    names = [".py", ".sql", ".j2", ".json"]
    files = [
        path.relative_to(ROOT).as_posix()
        for folder in ("src", "tools", "tests", "docs")
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and path.suffix in names and "__pycache__" not in path.parts
    ]
    assert files
    assert ignored(files) == set()


def test_future_packages_with_personal_sounding_names_are_not_ignored():
    paths = [
        "src/stealth/reports/monthly.py",
        "src/stealth/evidence/pack.py",
        "docs/screenshots/a.png",
    ]
    assert ignored(paths) == set()


@pytest.mark.parametrize(
    "path",
    [
        "stealth.db",
        "anywhere/nested/stealth.db",
        "stealth.db-wal",
        "key.wrapped",
        "outbox/acme-request.eml",
        "nested/message.eml",
        "evidence/acme/reply.eml",
        "profiles/abc123/stealth.db",
        "reports/2026-11.pdf",
        "screenshots/listing.png",
        "index.json",
        "brokers_extra.json",
        "logs/stealth.log",
        "stealth.log",
        ".env",
    ],
)
def test_personal_data_stays_out_of_git(path):
    assert ignored([path]) == {path}
