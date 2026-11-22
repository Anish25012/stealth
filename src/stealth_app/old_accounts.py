"""Find my old accounts: scan Gmail for sign-ups, then Keep or Delete my data for each company."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from stealth.cases.state import all_cases
from stealth.core.errors import ProfileError
from stealth.custom import store as custom
from stealth.oldaccounts import gmail
from stealth.oldaccounts import store as old
from stealth.oldaccounts.deletion import find_deletion_page
from stealth.profiles.models import GOOGLE_PROVIDER
from stealth.profiles.store import Profile
from stealth_app.dashboard import (
    State,
    changing,
    first,
    not_set_up,
    opened,
    read_form,
    redirect,
    render,
    start_job,
)
from stealth_app.discover import known_broker, start_lookup
from stealth_app.workspace import Job


def _uses_gmail(state: State, profile: Profile) -> bool:
    account = profile.mail_account()
    return state.workspace.demo or (account is not None and account.provider == GOOGLE_PROVIDER)


def _handled_by(state: State, profile: Profile, domain: str) -> str | None:
    """The case page for this company, if Stealth already writes to it."""
    cases = {c.broker_id for c in all_cases(profile.conn)}
    broker = known_broker(state, domain)
    if broker is not None:
        return f"/cases/{quote(broker.id)}" if broker.id in cases else "/cases"
    company_id = custom.company_id(domain)
    if company_id in cases:
        return f"/cases/{quote(company_id)}"
    return None


def install(app: FastAPI, state: State) -> None:
    @app.get("/old-accounts", response_class=HTMLResponse)
    def old_accounts_page(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return not_set_up(request)
            return render(
                request,
                "old_accounts.html",
                profile=profile,
                title="Find my old accounts",
                uses_gmail=_uses_gmail(state, profile),
                scan=old.scan_state(profile),
                undecided=old.undecided(profile),
                decided=old.decided(profile),
                batch=gmail.BATCH,
            )

    @app.post("/old-accounts/scan")
    async def old_accounts_scan(request: Request) -> Response:
        form = await read_form(request)
        further = first(form, "further") == "yes"
        with opened(state) as profile:
            if profile is None:
                raise ProfileError("There's no profile yet.")
            if not _uses_gmail(state, profile):
                raise ProfileError(
                    "Find my old accounts works with Gmail only for now.",
                    hint="Sign in with Google in Settings to use it.",
                )

        def work(profile: Profile, job: Job) -> None:
            source = state.workspace.signup_source(profile)
            job.lines.append(
                "Reading the sender, subject and date of sign-up style emails in your Gmail. "
                "Nothing else is read, and nothing is sent anywhere."
            )
            summary = old.run_scan(profile, source, further_back=further)
            job.lines.append(f"Read {summary.read} email(s).")
            job.lines.append(
                f"Found {summary.signups} compan{'y' if summary.signups == 1 else 'ies'} you "
                f"signed up with, {summary.new} of them new."
            )
            if summary.more:
                job.lines.append("There are older emails too: use Scan further back to read them.")

        job = start_job(state, "Finding your old accounts", work, "/old-accounts")
        return redirect(f"/jobs/{job.id}")

    @app.post("/old-accounts/{domain}/keep")
    async def old_account_keep(request: Request, domain: str) -> Response:
        await read_form(request)
        with changing(state) as profile:
            old.decide(profile, domain, old.KEEP)
        return redirect("/old-accounts")

    @app.post("/old-accounts/{domain}/undecide")
    async def old_account_undecide(request: Request, domain: str) -> Response:
        await read_form(request)
        with changing(state) as profile:
            old.decide(profile, domain, None)
        return redirect("/old-accounts")

    @app.get("/old-accounts/{domain}/delete", response_class=HTMLResponse)
    def old_account_confirm(request: Request, domain: str) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            account = old.get(profile, domain)
            return render(
                request,
                "old_account_delete.html",
                profile=profile,
                title=f"Delete your data at {account.name}?",
                account=account,
                handled_by=_handled_by(state, profile, account.domain),
            )

    @app.post("/old-accounts/{domain}/delete")
    async def old_account_delete(request: Request, domain: str) -> Response:
        form = await read_form(request)
        if first(form, "confirm") != "yes":
            raise ProfileError("Tick the box to confirm, or choose Keep instead.")
        with changing(state) as profile:
            account = old.decide(profile, domain, old.DELETE)
            handled = _handled_by(state, profile, account.domain)
            try:
                looked_up = custom.get(profile, custom.company_id(account.domain))
            except ProfileError:
                looked_up = None
        if handled is not None:
            return redirect(handled)
        if looked_up is not None:  # looked up before; an address still needs choosing
            return redirect(f"/companies/add/{quote(looked_up.id)}")
        job = start_lookup(state, account.domain, listing=None, category="other")
        return redirect(f"/jobs/{job.id}")

    @app.post("/old-accounts/{domain}/find-page")
    async def old_account_find_page(request: Request, domain: str) -> Response:
        await read_form(request)
        with opened(state) as profile:
            if profile is None:
                raise ProfileError("There's no profile yet.")
            account = old.get(profile, domain)

        def work(profile: Profile, job: Job) -> None:
            job.lines.append(f"Looking on {account.domain}'s own website for how to delete it...")
            url = find_deletion_page(account.domain, fetch=state.workspace.fetch)
            old.save_deletion_page(profile, account.domain, url)
            job.lines.append(f"Found: {url}" if url else "No account-deletion page was found.")

        job = start_job(state, f"Looking for {account.name}'s deletion page", work, "/old-accounts")
        return redirect(f"/jobs/{job.id}")
