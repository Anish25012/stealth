"""Signed broker lists, update checks and the scheduler's daily look for them."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from stealth.core.errors import RegistryError, StealthError
from stealth.registry import loader, signing, updater


def _key_pair():
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


@pytest.fixture
def key(monkeypatch):
    """A throwaway signing key, trusted in place of the project's real one."""
    private, public = _key_pair()
    monkeypatch.setattr(signing, "PUBLIC_KEY", public)
    return private


def _published(version, private, extra_broker=True):
    document = loader.packaged_document()
    document = {**document, "version": version}
    if extra_broker:
        first = dict(document["brokers"][0])
        first.update(id="newly-added", name="Newly Added Ltd", email="privacy@newly-added.test")
        document["brokers"] = [*document["brokers"], first]
    data = json.dumps(document).encode()
    return data, signing.sign(data, private).encode()


def _fetcher(data, sig, releases=b"[]"):
    def fetch(url):
        if url == updater.LIST_URL:
            return data
        if url == updater.SIGNATURE_URL:
            return sig
        if url.startswith("https://api.github.com/"):
            return releases
        raise AssertionError(url)

    return fetch


PACKAGED = loader.packaged_document()["version"]


# Signatures -------------------------------------------------------------------------------


def test_the_real_public_key_is_a_valid_ed25519_key():
    assert len(base64.b64decode(signing.PUBLIC_KEY)) == 32
    assert not signing.verify(b"anything", base64.b64encode(b"x" * 64))


def test_a_signature_only_matches_the_exact_bytes(key):
    sig = signing.sign(b"broker list", key)
    assert signing.verify(b"broker list", sig)
    assert not signing.verify(b"broker list ", sig)
    assert not signing.verify(b"broker list", "not base64!")


def test_a_list_signed_by_someone_else_is_refused(key, tmp_path):
    other, _ = _key_pair()
    data, sig = _published(PACKAGED + 1, other)
    with pytest.raises(RegistryError, match="isn't signed"):
        updater.update(PACKAGED, _fetcher(data, sig), tmp_path)
    assert not (tmp_path / "registry" / "brokers.json").exists()


def test_a_genuine_list_that_fails_the_schema_is_refused(key, tmp_path):
    data = json.dumps({"version": PACKAGED + 1, "brokers": [{"id": "x"}]}).encode()
    sig = signing.sign(data, key).encode()
    with pytest.raises(RegistryError, match="invalid"):
        updater.update(PACKAGED, _fetcher(data, sig), tmp_path)


# Updating the list ------------------------------------------------------------------------


def test_a_newer_signed_list_is_kept_and_used(key, tmp_path, monkeypatch):
    monkeypatch.setenv("STEALTH_HOME", str(tmp_path))
    data, sig = _published(PACKAGED + 1, key)
    outcome = updater.update(PACKAGED, _fetcher(data, sig))
    assert outcome.updated and outcome.version == PACKAGED + 1
    assert loader.current_document()["version"] == PACKAGED + 1
    assert "newly-added" in loader.load_registry(tmp_path / "none.json")


def test_an_older_or_equal_list_is_ignored(key, tmp_path):
    data, sig = _published(PACKAGED, key)
    assert not updater.update(PACKAGED, _fetcher(data, sig), tmp_path).updated
    assert not (tmp_path / "registry").exists()


def test_an_old_signed_list_cant_be_replayed(key, tmp_path):
    """Rollback: once version N+2 was accepted, a genuine N+1 is refused."""
    newer = _published(PACKAGED + 2, key)
    assert updater.update(PACKAGED, _fetcher(*newer), tmp_path).updated
    older = _published(PACKAGED + 1, key)
    assert not updater.update(PACKAGED, _fetcher(*older), tmp_path).updated
    assert loader.current_document(tmp_path)["version"] == PACKAGED + 2


def test_a_tampered_downloaded_copy_falls_back_to_the_built_in_list(key, tmp_path):
    data, sig = _published(PACKAGED + 1, key)
    updater.update(PACKAGED, _fetcher(data, sig), tmp_path)
    (tmp_path / "registry" / "brokers.json").write_bytes(data.replace(b"Newly", b"Evilly"))
    assert loader.current_document(tmp_path)["version"] == PACKAGED


def test_the_sign_tool_refuses_a_key_that_doesnt_match_the_app(tmp_path, monkeypatch):
    import runpy
    import sys

    private, _ = _key_pair()  # not the trusted key
    monkeypatch.setenv("BROKER_LIST_KEY", private)
    monkeypatch.setattr(sys, "argv", ["sign_broker_list.py", str(tmp_path)])
    with pytest.raises(SystemExit) as exit_:
        runpy.run_path("tools/sign_broker_list.py", run_name="__main__")
    assert exit_.value.code == 1
    assert not (tmp_path / "brokers.json.sig").exists()


# App updates ------------------------------------------------------------------------------


def _releases(*tags, prerelease=()):
    return json.dumps(
        [
            {
                "tag_name": t,
                "html_url": f"https://github.com/x/releases/tag/{t}",
                "draft": False,
                "prerelease": t in prerelease,
            }
            for t in tags
        ]
    ).encode()


def test_newest_release_skips_prereleases_and_the_broker_list():
    releases = json.loads(
        _releases("v0.1.0", "v0.3.0", "broker-list", "v0.9.0", prerelease={"v0.9.0"})
    )
    assert updates_module().newest_release(releases) == (
        "0.3.0",
        "https://github.com/x/releases/tag/v0.3.0",
    )


def updates_module():
    pytest.importorskip("fastapi")
    from stealth_app import updates

    return updates


def test_check_finds_a_newer_app_and_announces_it_once(key, tmp_path):
    updates = updates_module()
    data, sig = _published(PACKAGED, key)
    fetch = _fetcher(data, sig, _releases("v0.1.0", "v0.2.0"))
    first = updates.check(fetch, tmp_path, current="0.1.0")
    assert first.new_app == "0.2.0"
    assert first.state.latest_url.endswith("v0.2.0")
    assert "Stealth 0.2.0 is available" in first.state.result
    second = updates.check(fetch, tmp_path, current="0.1.0")
    assert second.new_app is None and second.state.latest_app == "0.2.0"


def test_check_when_up_to_date_and_list_updated(key, tmp_path):
    updates = updates_module()
    data, sig = _published(PACKAGED + 1, key)
    result = updates.check(_fetcher(data, sig, _releases("v0.1.0")), tmp_path, current="0.1.0")
    assert result.state.latest_app is None
    assert result.list_updated
    assert result.state.result.startswith("Stealth is up to date; downloaded broker list")


def test_check_records_a_failure_instead_of_raising(tmp_path):
    updates = updates_module()

    def offline(url):
        raise StealthError("Couldn't reach GitHub to check for updates.")

    result = updates.check(offline, tmp_path)
    assert result.state.result == "Couldn't check: Couldn't reach GitHub to check for updates."
    assert result.state.checked is not None
    assert updates.load(tmp_path).checked_at == result.state.checked_at


def test_checks_are_daily_and_can_be_turned_off(tmp_path):
    updates = updates_module()
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    state = updates.UpdateState()
    assert updates.due(state, now)
    state.checked_at = (now - timedelta(hours=2)).isoformat()
    assert not updates.due(state, now)
    state.checked_at = (now - timedelta(days=1, minutes=1)).isoformat()
    assert updates.due(state, now)
    updates.set_enabled(False, tmp_path)
    assert not updates.due(updates.load(tmp_path), now)


def test_the_scheduler_checks_reloads_the_list_and_mentions_a_new_version(store):
    updates = updates_module()
    from stealth_app.scheduler import Scheduler
    from stealth_app.workspace import RealWorkspace

    workspace = RealWorkspace(store)
    workspace.brokers()
    notices = []
    calls = []

    def check():
        calls.append(1)
        return updates.CheckResult(updates.UpdateState(), list_updated=True, new_app="0.2.0")

    start = datetime(2026, 11, 7, 12, tzinfo=UTC)
    times = iter([start, start + timedelta(hours=1)])
    scheduler = Scheduler(
        workspace,
        notify=lambda title, message: notices.append((title, message)),
        now=lambda: next(times),
        check_updates=check,
        update_state=updates.UpdateState,  # never checked: due
    )
    scheduler.tick()
    assert calls == [1]
    assert workspace._brokers is None  # the next use loads the new list
    assert notices == [
        ("Stealth update", "Stealth 0.2.0 is out. Open Settings in Stealth to download it.")
    ]


def test_without_a_checker_the_scheduler_never_goes_online(store):
    from stealth_app.scheduler import Scheduler
    from stealth_app.workspace import RealWorkspace

    scheduler = Scheduler(RealWorkspace(store))
    assert scheduler.check_updates is None
