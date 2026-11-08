"""First-run setup and the People page.

The wizard follows the brief's order: who this is for, their details, connecting email, the
recovery code, choosing companies, then letters written and waiting for approval. Nothing is saved
until the email connection has been proved, and the recovery code is held only until the person
says they've kept it.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field, replace
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from stealth.cases import engine
from stealth.core.errors import ProfileError, StealthError
from stealth.core.jurisdictions import choices
from stealth.mail import gmail_api, google_oauth
from stealth.profiles.models import (
    MICROSOFT_DOMAINS,
    PROVIDERS,
    MailAccount,
    Person,
    Settings,
    google_account,
    provider_account,
)
from stealth.registry.loader import counts, select_brokers
from stealth.setup import Consent, new_profile
from stealth_app import autostart
from stealth_app.dashboard import (
    State,
    changing,
    first,
    opened,
    read_form,
    redirect,
    render,
)

STEPS = ["Who it's for", "Your details", "Email", "Recovery code", "Companies", "Finish"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CONSENT_METHODS = [
    "They told me in person",
    "They agreed in writing (a letter, email or message)",
    "They agreed on the phone",
]


@dataclass
class Draft:
    """What the wizard has collected so far. Lives in memory only, for one launch."""

    for_self: bool = True
    helper_name: str = ""
    consent_method: str = ""
    person: Person | None = None
    profile_id: str | None = None
    recovery_code: str | None = None
    values: dict[str, str] = field(default_factory=dict)  # last form, to refill after an error
    google: google_oauth.SignIn | None = None  # a Google sign-in waiting for the browser


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _flat(form: dict[str, list[str]]) -> dict[str, str]:
    return {k: v[0] for k, v in form.items() if k != "csrf"}


def install(app: FastAPI, state: State) -> None:
    drafts: dict[str, Draft] = {}
    app.state.setup_drafts = drafts  # for tests that play the browser's part

    def draft() -> Draft:
        if state.workspace.demo:
            raise StealthError(
                "You're in the demo, which has its own made-up person.",
                hint="Leave the demo first, then set up Stealth for real.",
            )
        return drafts.setdefault("current", Draft())

    def step(request: Request, name: str, number: int, **context: Any) -> HTMLResponse:
        return render(
            request,
            name,
            title=f"Set up Stealth: {STEPS[number - 1]}",
            step=number,
            steps=STEPS,
            draft=drafts.get("current"),
            **context,
        )

    def profile_made() -> Draft:
        current = draft()
        if current.profile_id is None:
            raise ProfileError("Start setting up from the beginning.", hint="Go to Set up Stealth.")
        return current

    # 1. Who it's for -----------------------------------------------------------------------

    @app.get("/setup", response_class=HTMLResponse)
    def who_page(request: Request) -> Response:
        current = draft()
        if current.profile_id is not None:
            drafts.pop("current")  # a finished or abandoned run: start again cleanly
            current = draft()
        return step(request, "setup_who.html", 1, methods=CONSENT_METHODS, errors={}, values={})

    @app.post("/setup/who")
    async def who(request: Request) -> Response:
        form = _flat(await read_form(request))
        current = draft()
        errors = {}
        current.for_self = form.get("for") != "someone"
        if not current.for_self:
            if not form.get("helper_name", "").strip():
                errors["helper_name"] = "Enter your own name, so the record says who set this up."
            if form.get("consent_method") not in CONSENT_METHODS:
                errors["consent_method"] = "Choose how they agreed."
            if form.get("agreed") != "yes":
                errors["agreed"] = "Stealth can only write for someone who has agreed to it."
        if errors:
            return step(
                request, "setup_who.html", 1, methods=CONSENT_METHODS, errors=errors, values=form
            )
        current.helper_name = form.get("helper_name", "").strip()
        current.consent_method = form.get("consent_method", "")
        return redirect("/setup/details")

    # 2. Details ----------------------------------------------------------------------------

    @app.get("/setup/details", response_class=HTMLResponse)
    def details_page(request: Request) -> Response:
        draft()
        return step(request, "setup_details.html", 2, errors={}, values={}, countries=choices())

    @app.post("/setup/details")
    async def details(request: Request) -> Response:
        form = _flat(await read_form(request))
        current = draft()
        name = form.get("name", "").strip()
        emails = _lines(form.get("emails", ""))
        errors = {}
        if not name:
            errors["name"] = "Enter a full name."
        if not emails:
            errors["emails"] = "Enter at least one email address."
        elif bad := [e for e in emails if not EMAIL_RE.match(e)]:
            errors["emails"] = f"This doesn't look like an email address: {bad[0]}"
        country = form.get("country", "GB").upper()
        if country not in dict(choices()):
            errors["country"] = "Choose where they live from the list."
        if errors:
            return step(
                request, "setup_details.html", 2, errors=errors, values=form, countries=choices()
            )
        current.person = Person(
            display_name=name,
            country=country,
            names=[name, *_lines(form.get("other_names", ""))],
            emails=emails,
            addresses=_lines(form.get("addresses", "")),
            phones=_lines(form.get("phones", "")),
        )
        return redirect("/setup/email")

    # 3. Email ------------------------------------------------------------------------------

    def email_step(request: Request, errors: dict[str, str], values: dict[str, str]) -> Response:
        current = draft()
        if current.person is None:
            return redirect("/setup/details")
        values = {"address": current.person.emails[0], **values}
        return step(
            request,
            "setup_email.html",
            3,
            errors=errors,
            values=values,
            providers=PROVIDERS,
            google=google_oauth.client() is not None,
        )

    @app.get("/setup/email", response_class=HTMLResponse)
    def email_page(request: Request) -> Response:
        return email_step(request, {}, {})

    @app.post("/setup/email")
    async def email(request: Request) -> Response:
        form = _flat(await read_form(request))
        current = draft()
        if current.person is None:
            return redirect("/setup/details")
        errors: dict[str, str] = {}
        own = current.for_self or form.get("whose") == "theirs"
        account, password = _account_from(form, own=own, errors=errors)
        if errors or account is None:
            return email_step(request, errors, form)
        consent = None
        if not current.for_self:
            consent = Consent(
                given_by=current.person.display_name,
                method=current.consent_method,
                recorded_by=current.helper_name,
            )
        if not state.workspace.lock.acquire(blocking=False):
            raise StealthError("Stealth is busy with something else right now.")
        try:
            profile_id, code = new_profile(
                state.workspace.store,
                current.person,
                account,
                password,
                consent=consent,
            )
        except StealthError as exc:
            # The most common failure is the password: keep the form and say why, beside it.
            errors["password"] = exc.message + (f" {exc.hint}" if exc.hint else "")
            return email_step(request, errors, form)
        finally:
            state.workspace.lock.release()
        current.profile_id, current.recovery_code = profile_id, code
        return redirect("/setup/recovery")

    # 3b. Or sign in with Google ------------------------------------------------------------

    @app.post("/setup/email/google")
    async def google_start(request: Request) -> Response:
        await read_form(request)  # checks the CSRF token
        current = draft()
        client = google_oauth.client()
        if current.person is None or client is None:
            return redirect("/setup/email")
        if current.google is not None:
            current.google.close()
        current.google = google_oauth.start(client, hint=current.person.emails[0])
        google_oauth.open_browser(current.google.url)
        return redirect("/setup/email/google")

    @app.get("/setup/email/google", response_class=HTMLResponse)
    def google_wait(request: Request) -> Response:
        current = draft()
        sign_in = current.google
        if sign_in is None or current.person is None:
            return redirect("/setup/email")
        answer = sign_in.poll()
        if answer is None:
            return step(
                request,
                "setup_google.html",
                3,
                url=sign_in.url,
                cancel_url="/setup/email/google/cancel",
                cancel_label="Cancel and use an app password instead",
            )
        current.google = None
        try:
            address, token = gmail_api.finish_sign_in(sign_in, answer)
        except StealthError as exc:
            message = exc.message + (f" {exc.hint}" if exc.hint else "")
            return email_step(request, {"google": message}, {})
        # Signed in to an address of theirs: their mailbox. Otherwise it's the helper's own.
        own = current.for_self or address.lower() in {e.lower() for e in current.person.emails}
        consent = None
        if not current.for_self:
            consent = Consent(
                given_by=current.person.display_name,
                method=current.consent_method,
                recorded_by=current.helper_name,
            )
        if not state.workspace.lock.acquire(blocking=False):
            raise StealthError("Stealth is busy with something else right now.")
        try:
            profile_id, code = new_profile(
                state.workspace.store,
                current.person,
                google_account(address, is_own_mailbox=own),
                token,
                consent=consent,
                check_mail=False,  # finish_sign_in has just proved Gmail works
            )
        finally:
            state.workspace.lock.release()
        current.profile_id, current.recovery_code = profile_id, code
        return redirect("/setup/recovery")

    @app.post("/setup/email/google/cancel")
    async def google_cancel(request: Request) -> Response:
        await read_form(request)
        current = draft()
        if current.google is not None:
            current.google.close()
            current.google = None
        return redirect("/setup/email")

    # Signing in to Google again, from Settings ----------------------------------------------
    # Needed when Google stops honouring the sign-in (a password change, access removed, six
    # months unused), and to move an app-password Gmail profile over to Google sign-in.

    reconnecting: dict[str, google_oauth.SignIn] = {}
    app.state.google_reconnecting = reconnecting

    @app.post("/settings/google")
    async def reconnect_start(request: Request) -> Response:
        await read_form(request)
        client = google_oauth.client()
        if client is None or state.workspace.demo:
            return redirect("/settings")
        with opened(state) as profile:
            account = profile.mail_account() if profile else None
        if account is None:
            return redirect("/settings")
        if "current" in reconnecting:
            reconnecting.pop("current").close()
        sign_in = google_oauth.start(client, hint=account.address)
        reconnecting["current"] = sign_in
        google_oauth.open_browser(sign_in.url)
        return redirect("/settings/google")

    @app.get("/settings/google", response_class=HTMLResponse)
    def reconnect_wait(request: Request) -> Response:
        sign_in = reconnecting.get("current")
        if sign_in is None:
            return redirect("/settings")
        answer = sign_in.poll()
        if answer is None:
            return render(
                request,
                "setup_google.html",
                title="Sign in with Google",
                url=sign_in.url,
                cancel_url="/settings/google/cancel",
                cancel_label="Cancel",
            )
        reconnecting.pop("current", None)
        address, token = gmail_api.finish_sign_in(sign_in, answer)  # errors show as a page
        with changing(state) as profile:
            previous = profile.mail_account()
            own = previous.is_own_mailbox if previous else True
            profile.save_mail_account(google_account(address, is_own_mailbox=own), password=token)
        return redirect("/settings#mail-h")

    @app.post("/settings/google/cancel")
    async def reconnect_cancel(request: Request) -> Response:
        await read_form(request)
        if "current" in reconnecting:
            reconnecting.pop("current").close()
        return redirect("/settings")

    # 4. Recovery code -----------------------------------------------------------------------

    @app.get("/setup/recovery", response_class=HTMLResponse)
    def recovery_page(request: Request) -> Response:
        current = profile_made()
        if current.recovery_code is None:
            return redirect("/setup/companies")
        return step(request, "setup_recovery.html", 4, code=current.recovery_code, errors={})

    @app.post("/setup/recovery")
    async def recovery(request: Request) -> Response:
        form = _flat(await read_form(request))
        current = profile_made()
        if form.get("kept") != "yes" and current.recovery_code is not None:
            return step(
                request,
                "setup_recovery.html",
                4,
                code=current.recovery_code,
                errors={"kept": "Tick the box once you've written the code down or printed it."},
            )
        current.recovery_code = None  # never shown again
        return redirect("/setup/companies")

    # 5. Companies ---------------------------------------------------------------------------

    @app.get("/setup/companies", response_class=HTMLResponse)
    def companies_page(request: Request) -> Response:
        profile_made()
        with changing(state) as profile:
            brokers = state.workspace.brokers()
            jurisdiction = engine.jurisdiction_of(profile)
            settings = profile.settings()
            core = [s.broker for s in select_brokers(brokers, jurisdiction, _everything(settings))]
            return step(
                request,
                "setup_companies.html",
                5,
                numbers=counts(brokers, jurisdiction),
                core=sorted(core, key=lambda b: b.name.lower()),
                settings=settings,
                profile=profile,
            )

    @app.post("/setup/companies")
    async def companies(request: Request) -> Response:
        form = await read_form(request)
        profile_made()
        with changing(state) as profile:
            brokers = state.workspace.brokers()
            jurisdiction = engine.jurisdiction_of(profile)
            everything = _everything(profile.settings())
            offered = {s.broker.id for s in select_brokers(brokers, jurisdiction, everything)}
            kept = set(form.get("broker", []))
            profile.save_settings(
                include_extended=first(form, "extended") == "yes",
                skip_brokers=sorted(offered - kept),
            )
        return redirect("/setup/finish")

    # 6. Finish ------------------------------------------------------------------------------

    @app.get("/setup/finish", response_class=HTMLResponse)
    def finish_page(request: Request) -> Response:
        profile_made()
        with changing(state) as profile:
            brokers = state.workspace.brokers()
            due = engine.due_selections(
                profile,
                select_brokers(brokers, engine.jurisdiction_of(profile), profile.settings()),
            )
            by_email = sum(1 for s in due if s.broker.method == "email" and s.broker.email)
            return step(
                request,
                "setup_finish.html",
                6,
                by_email=by_email,
                by_form=len(due) - by_email,
                person=profile.person(),
                account=profile.mail_account(),
                autostart_available=autostart.available(),
                hour=profile.settings().auto_run_hour,
            )

    @app.post("/setup/finish")
    async def finish(request: Request) -> Response:
        form = await read_form(request)
        profile_made()
        with changing(state) as profile:
            engine.open_cases(profile, state.workspace.brokers(), hold=True)
        if first(form, "autostart") == "yes":
            autostart.set_enabled(True)
        drafts.pop("current", None)
        return redirect("/approve")

    # People -------------------------------------------------------------------------------

    @app.get("/people", response_class=HTMLResponse)
    def people(request: Request) -> Response:
        store = state.workspace.store
        active = store.active_id() if store.list_ids() else None
        people = []
        for profile_id in store.list_ids():
            with store.open(profile_id) as profile:
                people.append(
                    {
                        "id": profile_id,
                        "name": profile.person().display_name,
                        "account": profile.mail_account(),
                        "consents": profile.consents(),
                        "active": profile_id == active,
                    }
                )
        return render(request, "people.html", title="People", people=people)

    @app.post("/people/switch")
    async def switch(request: Request) -> Response:
        form = await read_form(request)
        state.workspace.store.set_active(first(form, "profile"))
        return redirect("/")

    @app.post("/people/delete")
    async def delete(request: Request) -> Response:
        form = await read_form(request)
        if first(form, "confirm").strip().upper() != "DELETE":
            raise ProfileError(
                "Nothing was deleted.", hint="To delete someone, type DELETE in the box first."
            )
        if not state.workspace.lock.acquire(blocking=False):
            raise StealthError("Stealth is busy with something else right now.")
        try:
            state.workspace.store.delete(first(form, "profile"))
        finally:
            state.workspace.lock.release()
        return redirect("/people")


def _everything(settings: Settings) -> Settings:
    """The default companies with nothing skipped, so the list can show what's been unticked."""
    return replace(settings, skip_brokers=[], include_extended=False)


def _account_from(
    form: dict[str, str], *, own: bool, errors: dict[str, str]
) -> tuple[MailAccount | None, str]:
    """Turn the email form into account settings, filling `errors` with anything wrong."""
    address = form.get("address", "").strip()
    password = form.get("password", "")
    choice = form.get("provider", "")
    if not EMAIL_RE.match(address):
        errors["address"] = "Enter the email address the letters will come from."
    if choice == "outlook" or (
        choice in PROVIDERS and address.rsplit("@", 1)[-1].lower() in MICROSOFT_DOMAINS
    ):
        errors["provider"] = (
            "Outlook and Hotmail addresses need Microsoft sign-in, which is coming in the next "
            "update. For now, use a Gmail, Yahoo or iCloud address."
        )
        return None, ""
    if not password.strip():
        errors["password"] = "Enter the app password."  # noqa: S105 - a message, not a password
    if choice in PROVIDERS:
        if choice == "gmail":
            password = password.replace(" ", "")  # Google shows app passwords in groups of four
        preset = provider_account(PROVIDERS[choice], address, is_own_mailbox=own)
        return (None if errors else preset), password
    if choice != "other":
        errors["provider"] = "Choose your email provider."
        return None, ""
    account = _manual_account(form, address, own=own, errors=errors)
    return (None if errors else account), password


def _manual_account(
    form: dict[str, str], address: str, *, own: bool, errors: dict[str, str]
) -> MailAccount | None:
    values: dict[str, Any] = {}
    for prefix, default_port in (("smtp", 587), ("imap", 993)):
        host = form.get(f"{prefix}_host", "").strip()
        security = form.get(f"{prefix}_security", "")
        port_text = form.get(f"{prefix}_port", "").strip() or str(default_port)
        if not host:
            errors[f"{prefix}_host"] = "Enter the server name, from your provider's help pages."
        if not port_text.isdigit() or not 0 < int(port_text) < 65536:
            errors[f"{prefix}_port"] = "Enter a port number, such as 587 or 993."
        allowed = ("starttls", "ssl", "none") if prefix == "smtp" else ("ssl", "none")
        if security not in allowed:
            errors[f"{prefix}_security"] = "Choose how the connection is protected."
        elif security == "none" and host and not _is_loopback(host):
            # An unprotected connection would send the password across the network as plain text.
            errors[f"{prefix}_security"] = "Choose a protected connection (SSL or STARTTLS)."
        values[prefix] = (host, int(port_text) if port_text.isdigit() else 0, security)
    if errors:
        return None
    (smtp_host, smtp_port, smtp_security), (imap_host, imap_port, imap_security) = (
        values["smtp"],
        values["imap"],
    )
    return MailAccount(
        provider="smtp",
        address=address,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_security=smtp_security,
        imap_host=imap_host,
        imap_port=imap_port,
        imap_security=imap_security,
        username=form.get("username", "").strip() or address,
        is_own_mailbox=own,
    )
