"""Turning templates into letters.

The first line of every template is `Subject: ...`, so a letter's subject and body are reviewed
together in one file. Rendering is strict: a missing value is an error, never a blank in a legal
letter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import jinja2

from stealth.core.errors import TemplateRenderError
from stealth.core.jurisdictions import Jurisdiction
from stealth.profiles.models import MailAccount, Person

_ENV = jinja2.Environment(
    loader=jinja2.PackageLoader("stealth", "templates"),
    undefined=jinja2.StrictUndefined,
    autoescape=False,  # noqa: S701 - plain-text email, not HTML
    keep_trailing_newline=True,
)


@dataclass(frozen=True)
class Letter:
    subject: str
    body: str


def format_date(value: date) -> str:
    """A date the way a UK letter writes it: 3 March 2027."""
    return f"{value.day} {value.strftime('%B %Y')}"


def render(jurisdiction: Jurisdiction, template: str, **context: Any) -> Letter:
    name = f"{jurisdiction.template_dir}/{template}.j2"
    try:
        text = _ENV.get_template(name).render(**context)
    except jinja2.TemplateNotFound as exc:
        raise TemplateRenderError(f"Stealth has no letter called {template}.") from exc
    except jinja2.UndefinedError as exc:
        raise TemplateRenderError(f"A letter is missing a detail: {exc.message}") from exc
    first, _, rest = text.partition("\n")
    if not first.startswith("Subject: "):
        raise TemplateRenderError(f"Template {name} must start with a Subject line.")
    return Letter(subject=first.removeprefix("Subject: ").strip(), body=rest.lstrip("\n"))


def identity_context(person: Person, account: MailAccount, *, extended: bool) -> dict[str, Any]:
    """The personal details a letter may contain.

    This is the single choke point for what leaves the computer. Extended-tier companies may owe
    this person nothing, so they get name and email only, the least they need to find a record.
    Date of birth appears only after an explicit opt-in.
    """
    if not person.names or not person.emails:
        raise TemplateRenderError(
            "Add at least one name and one email address to your profile first."
        )
    include_dob = person.dob_opt_in and person.date_of_birth and not extended
    return {
        "person_name": person.display_name,
        "sign_off": person.display_name
        if account.is_own_mailbox
        else f"On behalf of {person.display_name}",
        "on_behalf": not account.is_own_mailbox,
        "extended": extended,
        "names": person.names,
        "emails": person.emails,
        "addresses": [] if extended else person.addresses,
        "phones": [] if extended else person.phones,
        "date_of_birth": person.date_of_birth if include_dob else None,
    }
