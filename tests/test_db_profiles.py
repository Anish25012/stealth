import pytest
import sqlcipher3

from stealth.core.errors import DatabaseKeyError, ProfileError
from stealth.db import connection, events
from stealth.profiles import keys
from stealth.profiles.models import MailAccount, Person
from stealth.profiles.store import ProfileStore


def test_database_file_is_encrypted_on_disk(store: ProfileStore, person: Person):
    pid, _ = store.create()
    with store.open(pid) as p:
        p.save_person(person)
    raw = (store.root / pid / "stealth.db").read_bytes()
    assert b"SQLite format 3" not in raw
    assert b"test.person@example.org" not in raw


def test_wrong_key_is_reported_in_plain_english(tmp_path):
    path = tmp_path / "x.db"
    connection.open_db(path, b"a" * 32).execute("CREATE TABLE t (a)").connection.close()
    with pytest.raises(DatabaseKeyError) as err:
        connection.open_db(path, b"b" * 32)
    assert "unlock" in err.value.message


def test_short_key_rejected(tmp_path):
    with pytest.raises(ValueError):
        connection.open_db(tmp_path / "x.db", b"short")


def test_migrations_are_idempotent_and_set_version(store: ProfileStore):
    pid, _ = store.create()
    with store.open(pid) as p:
        latest = connection.available_migrations()[-1][0]
        assert connection.migrate(p.conn) == latest
        assert p.conn.execute("PRAGMA user_version").fetchone()[0] == latest


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    conn = connection.open_db(tmp_path / "x.db", b"a" * 32)
    monkeypatch.setattr(
        connection,
        "available_migrations",
        lambda: [(1, "bad.sql", "CREATE TABLE ok (a); CREATE TABLE ok (a);")],
    )
    with pytest.raises(sqlcipher3.Error):
        connection.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


def test_event_log_rejects_update_and_delete(profile):
    events.append(profile.conn, "something", detail="x")
    for sql in ("UPDATE event SET type = 'edited'", "DELETE FROM event"):
        with pytest.raises(sqlcipher3.IntegrityError, match="append-only"):
            profile.conn.execute(sql)


def test_event_chain_verifies_and_detects_tampering(profile):
    for i in range(5):
        events.append(profile.conn, "step", n=i)
    assert events.verify_chain(profile.conn)
    # A determined editor can drop the trigger. The chain must still catch them.
    profile.conn.execute("DROP TRIGGER event_no_update")
    profile.conn.execute("UPDATE event SET payload = '{\"n\": 99}' WHERE id = 3")
    assert not events.verify_chain(profile.conn)


def test_two_profiles_cannot_read_each_other(store: ProfileStore, person: Person):
    a, _ = store.create()
    b, _ = store.create()
    with store.open(a) as p:
        p.save_person(person)
    with store.open(b) as p:
        p.save_person(Person(display_name="Someone Else", emails=["else@example.org"]))
    key_b = keys.load_db_key(store.secrets, b)
    with pytest.raises(DatabaseKeyError):
        connection.open_db(store.root / a / "stealth.db", key_b)
    with store.open(b) as p:
        assert p.person().emails == ["else@example.org"]


def test_index_file_holds_no_personal_data(store: ProfileStore, person: Person):
    pid, _ = store.create()
    with store.open(pid) as p:
        p.save_person(person)
    assert "Test Person" not in store.index_path.read_text("utf-8")


def test_recovery_code_restores_a_lost_key(store: ProfileStore, person: Person):
    pid, code = store.create()
    with store.open(pid) as p:
        p.save_person(person)
    store.secrets.delete(keys.key_name(pid))
    with pytest.raises(DatabaseKeyError), store.open(pid):
        pass
    store.restore_key(pid, code.lower().replace("-", " "))  # forgiving of how it's typed
    with store.open(pid) as p:
        assert p.person().display_name == "Test Person"


def test_wrong_recovery_code_is_rejected(store: ProfileStore):
    pid, _ = store.create()
    with pytest.raises(DatabaseKeyError):
        store.restore_key(pid, keys.new_recovery_code())


def test_recovery_code_alphabet_is_unambiguous():
    code = keys.new_recovery_code()
    assert len(code) == 29
    assert not set(code) & set("ILOU")


def test_delete_removes_folder_and_keys(store: ProfileStore):
    a, _ = store.create()
    b, _ = store.create()
    store.delete(a)
    assert not (store.root / a).exists()
    assert store.secrets.get(keys.key_name(a)) is None
    assert store.active_id() == b
    store.delete(b)
    with pytest.raises(ProfileError):
        store.active_id()


def test_unknown_profile_errors(store: ProfileStore):
    with pytest.raises(ProfileError), store.open("missing"):
        pass
    with pytest.raises(ProfileError):
        store.set_active("missing")
    with pytest.raises(ProfileError):
        store.delete("missing")


def test_person_roundtrip_and_dob_off_by_default(profile, person):
    assert profile.person() == person
    assert profile.person().dob_opt_in is False


def test_person_required_before_use(store: ProfileStore):
    pid, _ = store.create()
    with store.open(pid) as p, pytest.raises(ProfileError):
        p.person()


def test_settings_defaults_and_overrides(profile):
    assert profile.settings().daily_send_limit == 40
    assert profile.settings().resend_days_people_search == 60
    assert profile.settings().resend_days_other == 90
    profile.save_settings(daily_send_limit=10, skip_brokers=["x"])
    assert profile.settings().daily_send_limit == 10
    assert profile.settings().skip_brokers == ["x"]
    with pytest.raises(ProfileError):
        profile.save_settings(nonsense=1)


def test_mail_password_goes_to_keychain_not_database(store: ProfileStore):
    pid, _ = store.create()
    with store.open(pid) as p:
        p.save_mail_account(MailAccount("smtp", "a@example.org"), password="hunter2")
        assert store.secrets.get(p.mail_secret_name) == "hunter2"
        columns = [r[1] for r in p.conn.execute("PRAGMA table_info(mail_account)")]
        assert not any("password" in c for c in columns)
        assert p.mail_account().address == "a@example.org"


def test_consent_is_recorded_and_audited(profile):
    profile.record_consent("Parent", given_by="Parent", method="in person")
    assert profile.consents()[0]["subject_name"] == "Parent"
    assert profile.conn.execute("SELECT type FROM event").fetchone()[0] == "consent_recorded"
