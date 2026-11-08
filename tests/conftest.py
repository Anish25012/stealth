from collections.abc import Iterator
from pathlib import Path

import pytest

from stealth.core import clock
from stealth.core.secrets import MemoryStore
from stealth.profiles.models import MailAccount, Person
from stealth.profiles.store import Profile, ProfileStore


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
