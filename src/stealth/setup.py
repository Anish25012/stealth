"""Creating a complete profile in one step, from what a setup screen or prompt collected.

The mail connection is checked before anything is saved, so a mistyped password never leaves a
half-made profile behind.
"""

from __future__ import annotations

from dataclasses import dataclass

from stealth.mail.smtp_imap import check_connection
from stealth.profiles.models import MailAccount, Person
from stealth.profiles.store import ProfileStore


@dataclass(frozen=True)
class Consent:
    """Someone agreeing that Stealth may write to companies for them."""

    given_by: str  # the person themselves
    method: str  # how they agreed, in the words of whoever set this up
    recorded_by: str  # whoever is setting Stealth up for them


def new_profile(
    store: ProfileStore,
    person: Person,
    account: MailAccount,
    password: str,
    *,
    consent: Consent | None = None,
    check_mail: bool = True,
) -> tuple[str, str]:
    """Create a profile and return (profile id, recovery code). The code is shown only once."""
    if check_mail:
        check_connection(account, password)
    profile_id, code = store.create()
    with store.open(profile_id) as profile:
        profile.save_person(person)
        profile.save_mail_account(account, password=password)
        if consent is not None:
            profile.record_consent(
                person.display_name,
                given_by=consent.given_by,
                method=f"{consent.method} (recorded by {consent.recorded_by})",
            )
    store.set_active(profile_id)
    return profile_id, code
