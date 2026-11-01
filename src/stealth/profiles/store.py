"""Profiles: one person each, one encrypted database each.

Keeping each person in their own database makes separation physical: one profile's code path
cannot read another's rows, and deleting a person means deleting a folder. `index.json` holds only
opaque ids, so nothing personal is readable without a key.
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stealth.core import clock, paths
from stealth.core.errors import ProfileError
from stealth.core.secrets import SecretStore
from stealth.db import events
from stealth.db.connection import Connection, migrate, open_db
from stealth.profiles import keys
from stealth.profiles.models import DEFAULT_SETTINGS, MailAccount, Person, Settings

CONSENT_WORDING_VERSION = "1"


@dataclass
class Profile:
    """An open profile. Use it through `ProfileStore.open` so the database gets closed."""

    id: str
    folder: Path
    conn: Connection
    secrets: SecretStore

    @property
    def outbox(self) -> Path:
        return paths.ensure_private_dir(self.folder / "outbox")

    @property
    def evidence(self) -> Path:
        return paths.ensure_private_dir(self.folder / "evidence")

    # Person ---------------------------------------------------------------------------------

    def person(self) -> Person:
        row = self.conn.execute("SELECT * FROM person").fetchone()
        if row is None:
            raise ProfileError("This profile has no personal details yet.")
        values: dict[str, list[str]] = {"name": [], "email": [], "address": [], "phone": []}
        for item in self.conn.execute("SELECT kind, value FROM identifier ORDER BY id"):
            values[item["kind"]].append(item["value"])
        return Person(
            display_name=row["display_name"],
            country=row["country"],
            names=values["name"],
            emails=values["email"],
            addresses=values["address"],
            phones=values["phone"],
            date_of_birth=row["date_of_birth"],
            dob_opt_in=bool(row["dob_opt_in"]),
        )

    def save_person(self, person: Person) -> None:
        """Replace the stored personal details."""
        self.conn.execute("BEGIN")
        try:
            self.conn.execute("DELETE FROM person")
            self.conn.execute(
                "INSERT INTO person (id, display_name, country, date_of_birth, dob_opt_in) "
                "VALUES (1, ?, ?, ?, ?)",
                (
                    person.display_name,
                    person.country,
                    person.date_of_birth,
                    int(person.dob_opt_in),
                ),
            )
            self.conn.execute("DELETE FROM identifier")
            for kind, items in (
                ("name", person.names),
                ("email", person.emails),
                ("address", person.addresses),
                ("phone", person.phones),
            ):
                for value in dict.fromkeys(items):
                    self.conn.execute(
                        "INSERT INTO identifier (kind, value) VALUES (?, ?)", (kind, value)
                    )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def record_consent(self, subject_name: str, given_by: str, method: str) -> None:
        self.conn.execute(
            "INSERT INTO consent (subject_name, given_by, method, wording_version, given_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (subject_name, given_by, method, CONSENT_WORDING_VERSION, clock.now().isoformat()),
        )
        events.append(self.conn, "consent_recorded", subject=subject_name, given_by=given_by)

    def consents(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM consent ORDER BY id")]

    # Settings -------------------------------------------------------------------------------

    def settings(self) -> Settings:
        stored = {
            r["key"]: json.loads(r["value"])
            for r in self.conn.execute("SELECT key, value FROM setting")
        }
        return Settings(**{**DEFAULT_SETTINGS, **stored})

    def save_settings(self, **changes: Any) -> None:
        for key, value in changes.items():
            if key not in DEFAULT_SETTINGS:
                raise ProfileError(f"Unknown setting: {key}")
            self.conn.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    # Mail account ---------------------------------------------------------------------------

    def mail_account(self) -> MailAccount | None:
        row = self.conn.execute("SELECT * FROM mail_account").fetchone()
        if row is None:
            return None
        return MailAccount(**{k: row[k] for k in row.keys() if k != "id"})  # noqa: SIM118

    def save_mail_account(self, account: MailAccount, password: str | None = None) -> None:
        """Store mail settings. The password goes to the keychain, never the database."""
        self.conn.execute("DELETE FROM mail_account")
        self.conn.execute(
            "INSERT INTO mail_account (id, provider, address, smtp_host, smtp_port, smtp_security,"
            " imap_host, imap_port, imap_security, username, is_own_mailbox)"
            " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account.provider,
                account.address,
                account.smtp_host,
                account.smtp_port,
                account.smtp_security,
                account.imap_host,
                account.imap_port,
                account.imap_security,
                account.username,
                int(account.is_own_mailbox),
            ),
        )
        if password is not None:
            self.secrets.set(self.mail_secret_name, password)

    @property
    def mail_secret_name(self) -> str:
        return f"mail-password:{self.id}"


class ProfileStore:
    """Creates, opens and deletes profiles under the app-data folder."""

    def __init__(self, secrets: SecretStore, home: Path | None = None) -> None:
        self.secrets = secrets
        self.home = home or paths.app_home()
        self.root = paths.profiles_dir(self.home)
        self.index_path = self.home / "index.json"

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"profiles": [], "active": None}
        data: dict[str, Any] = json.loads(self.index_path.read_text("utf-8"))
        return data

    def _write_index(self, index: dict[str, Any]) -> None:
        paths.ensure_private_dir(self.home)
        temp = self.index_path.with_suffix(".tmp")
        temp.write_text(json.dumps(index, indent=2), encoding="utf-8")
        temp.replace(self.index_path)

    def list_ids(self) -> list[str]:
        return list(self._read_index()["profiles"])

    def active_id(self) -> str:
        index = self._read_index()
        if not index["active"]:
            raise ProfileError("There's no profile yet.", hint="Run `stealth init` to create one.")
        return str(index["active"])

    def set_active(self, profile_id: str) -> None:
        index = self._read_index()
        if profile_id not in index["profiles"]:
            raise ProfileError(f"No profile with id {profile_id}.")
        self._write_index({**index, "active": profile_id})

    def create(self) -> tuple[str, str]:
        """Create an empty profile. Returns (id, recovery code); the code is shown only once."""
        profile_id = uuid.uuid4().hex[:12]
        paths.ensure_private_dir(self.home)
        paths.ensure_private_dir(self.root)
        folder = paths.ensure_private_dir(self.root / profile_id)
        db_key = keys.new_db_key()
        code = keys.new_recovery_code()
        keys.save_db_key(self.secrets, profile_id, db_key)
        keys.wrap_key(db_key, code, folder / "key.wrapped")
        conn = open_db(folder / "stealth.db", db_key)
        migrate(conn)
        conn.close()
        index = self._read_index()
        index["profiles"].append(profile_id)
        index["active"] = index["active"] or profile_id
        self._write_index(index)
        return profile_id, code

    @contextmanager
    def open(self, profile_id: str | None = None) -> Iterator[Profile]:
        """Open a profile (the active one by default), applying any pending migrations."""
        pid = profile_id or self.active_id()
        folder = self.root / pid
        if pid not in self.list_ids() or not folder.exists():
            raise ProfileError(f"No profile with id {pid}.")
        conn = open_db(folder / "stealth.db", keys.load_db_key(self.secrets, pid))
        try:
            migrate(conn)
            yield Profile(id=pid, folder=folder, conn=conn, secrets=self.secrets)
        finally:
            conn.close()

    def restore_key(self, profile_id: str, code: str) -> None:
        """Put a profile's key back in the keychain using its recovery code."""
        key = keys.unwrap_key(code, self.root / profile_id / "key.wrapped")
        keys.save_db_key(self.secrets, profile_id, key)

    def delete(self, profile_id: str) -> None:
        """Permanently remove a profile, its evidence and its keys."""
        if profile_id not in self.list_ids():
            raise ProfileError(f"No profile with id {profile_id}.")
        shutil.rmtree(self.root / profile_id)
        self.secrets.delete(keys.key_name(profile_id))
        self.secrets.delete(f"mail-password:{profile_id}")
        index = self._read_index()
        index["profiles"].remove(profile_id)
        if index["active"] == profile_id:
            index["active"] = index["profiles"][0] if index["profiles"] else None
        self._write_index(index)
