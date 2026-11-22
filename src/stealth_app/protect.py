"""The "Protect yourself" hub and the alerts every page can show."""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from stealth import alerts
from stealth.breaches import store as breaches
from stealth.breaches.provider import ManualProvider, manual_breach
from stealth.core.errors import ProfileError
from stealth.profiles.store import Profile
from stealth.protect import checklist, cifas, credit
from stealth.protect import store as protect
from stealth.protect.checklist import Link
from stealth.templates.render import format_date
from stealth_app import views
from stealth_app.dashboard import (
    State,
    changing,
    first,
    not_set_up,
    opened,
    raise_due_alerts,
    read_form,
    redirect,
    render,
    start_job,
)
from stealth_app.workspace import Job

# Checked against haveibeenpwned.com on 22 November 2026. HIBP's breach data is licensed
# CC BY 4.0, which asks for visible credit and a link wherever it's shown.
BREACH_LINKS = {
    "hibp": Link("Have I Been Pwned", "https://haveibeenpwned.com/", "The service's own site"),
    "key": Link(
        "Get an API key (Have I Been Pwned)",
        "https://haveibeenpwned.com/API/Key",
        "Linked from HIBP's API documentation",
    ),
    "licence": Link(
        "CC BY 4.0",
        "https://creativecommons.org/licenses/by/4.0/",
        "Named in HIBP's API documentation",
    ),
}


def _back(next_url: str) -> str:
    """Only ever a page in Stealth: never another site, even one named in a crafted form."""
    if next_url.startswith("/") and not next_url.startswith(("//", "/\\")):
        return next_url
    return "/"


def _days(chosen: str) -> int:
    try:
        return int(chosen)
    except ValueError:
        raise ProfileError("Choose when to be reminded from the list.") from None


def install(app: FastAPI, state: State) -> None:
    @app.get("/protect", response_class=HTMLResponse)
    def protect_page(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return not_set_up(request)
            raise_due_alerts(state, profile)
            entries = protect.entries(profile)
            first_open = next((i for i, e in enumerate(entries, 1) if not e.state), 0)
            return render(
                request,
                "protect.html",
                profile=profile,
                title="Protect yourself",
                entries=entries,
                first_open=first_open,
                score=protect.score(profile),
                alerts=alerts.open_alerts(profile),
                remind_choices=protect.REMIND_CHOICES,
                checked_on=format_date(date.fromisoformat(checklist.CHECKED_ON)),
                credit=credit.statuses(profile),
                credit_every=credit.EVERY_CHOICES,
                credit_default=credit.DEFAULT_EVERY,
                credit_advice=credit.ICO_ADVICE,
                note_limit=credit.NOTE_LIMIT,
                cifas=cifas.status(profile, views.today_for(profile)),
                cifas_about=cifas.ABOUT,
                cifas_apply=cifas.APPLY,
                today=views.today_for(profile),
                breach_settings=profile.settings(),
                breach_has_key=breaches.has_key(profile),
                breach_list=breaches.all_found(profile),
                breach_open=breaches.open_count(profile),
                breach_every=breaches.EVERY_CHOICES,
                breach_addresses=breaches.addresses(profile),
                breach_links=BREACH_LINKS,
            )

    # Data breaches.

    @app.post("/protect/breaches/settings")
    async def breach_settings(request: Request) -> Response:
        form = await read_form(request)
        with changing(state) as profile:
            breaches.configure(
                profile,
                on=first(form, "on") == "yes",
                provider=first(form, "provider"),
                every_days=_days(first(form, "every")),
            )
        return redirect("/protect#breaches")

    @app.post("/protect/breaches/key")
    async def breach_key(request: Request) -> Response:
        form = await read_form(request)
        with changing(state) as profile:
            breaches.save_key(profile, first(form, "api_key"))
            alerts.dismiss_topic(profile, "breach-check:key")
        return redirect("/protect#breaches")

    @app.post("/protect/breaches/key/forget")
    async def breach_key_forget(request: Request) -> Response:
        await read_form(request)
        with changing(state) as profile:
            breaches.forget_key(profile)
        return redirect("/protect#breaches")

    @app.post("/protect/breaches/check")
    async def breach_check(request: Request) -> Response:
        await read_form(request)
        with opened(state) as profile:
            if profile is None:
                raise ProfileError("There's no profile yet.")
            if not breaches.has_key(profile):
                raise ProfileError(
                    "Add your Have I Been Pwned API key first.",
                    hint="Or choose to check for yourself and add what you find.",
                )

        def work(profile: Profile, job: Job) -> None:
            key = breaches.api_key(profile)
            if key is None:
                raise ProfileError("Add your Have I Been Pwned API key first.")
            provider = state.workspace.breach_provider(key)
            job.lines.append(f"Asking {provider.name} about your email addresses...")
            summary = breaches.run_check(profile, provider, sleep=state.workspace.sleep)
            alerts.dismiss_topic(profile, "breach-check:key")
            job.lines.append(summary.line)
            for found in summary.new:
                job.lines.append(f"New: {found.breach.title}.")

        job = start_job(state, "Checking for data breaches", work, "/protect#breaches")
        return redirect(f"/jobs/{job.id}")

    @app.post("/protect/breaches/add")
    async def breach_add(request: Request) -> Response:
        form = await read_form(request)
        when = first(form, "date").strip()
        try:
            breach_date = date.fromisoformat(when) if when else None
        except ValueError:
            raise ProfileError("Enter the date as shown, or leave it empty.") from None
        breach = manual_breach(
            first(form, "title"), breach_date=breach_date, exposed=first(form, "exposed")
        )
        with changing(state) as profile:
            email = first(form, "email").strip().lower()
            if email not in breaches.addresses(profile):
                raise ProfileError("Choose one of your email addresses.")
            if breach_date is not None and breach_date > views.today_for(profile):
                raise ProfileError("The breach date can't be in the future.")
            provider = ManualProvider({email: [breach]})
            breaches.record(profile, email, provider.check(email), provider.source)
        return redirect("/protect#breaches")

    @app.post("/protect/breaches/{found_id}/dealt")
    async def breach_dealt(request: Request, found_id: int) -> Response:
        form = await read_form(request)
        with changing(state) as profile:
            breaches.mark_dealt_with(profile, found_id, first(form, "dealt") == "yes")
        return redirect(f"/protect#breach-{found_id}")

    # Cifas Protective Registration.

    @app.post("/protect/cifas/record")
    async def cifas_record(request: Request) -> Response:
        form = await read_form(request)
        try:
            started = date.fromisoformat(first(form, "started").strip())
        except ValueError:
            raise ProfileError("Enter the date your registration started.") from None
        with changing(state) as profile:
            cifas.record(profile, started, views.today_for(profile))
        return redirect("/protect#cifas")

    @app.post("/protect/cifas/forget")
    async def cifas_forget(request: Request) -> Response:
        await read_form(request)
        with changing(state) as profile:
            cifas.forget(profile)
        return redirect("/protect#cifas")

    # Credit reports. These paths have one more part than /protect/{item_id}/..., so they can't
    # be mistaken for a checklist item.

    @app.post("/protect/credit/{agency_id}/checked")
    async def credit_checked(request: Request, agency_id: str) -> Response:
        form = await read_form(request)
        days = _days(first(form, "every"))
        with changing(state) as profile:
            credit.mark_checked(profile, agency_id, views.today_for(profile), days)
        return redirect(f"/protect#credit-{agency_id}")

    @app.post("/protect/credit/{agency_id}/remind")
    async def credit_remind(request: Request, agency_id: str) -> Response:
        form = await read_form(request)
        chosen = first(form, "every")
        with changing(state) as profile:
            if chosen == "off":
                credit.stop_reminders(profile, agency_id)
            else:
                credit.set_every(profile, agency_id, _days(chosen), views.today_for(profile))
        return redirect(f"/protect#credit-{agency_id}")

    @app.post("/protect/credit/{agency_id}/note")
    async def credit_note(request: Request, agency_id: str) -> Response:
        form = await read_form(request)
        with changing(state) as profile:
            credit.save_note(profile, agency_id, first(form, "note"))
        return redirect(f"/protect#credit-{agency_id}")

    @app.post("/protect/{item_id}/mark")
    async def protect_mark(request: Request, item_id: str) -> Response:
        form = await read_form(request)
        chosen = first(form, "state")
        states = {"done": protect.DONE, "not_for_me": protect.NOT_FOR_ME, "todo": None}
        if chosen not in states:
            raise ProfileError("Choose done, not for me, or still to do.")
        with changing(state) as profile:
            protect.mark(profile, item_id, states[chosen], views.today_for(profile))
        return redirect(f"/protect#{item_id}")

    @app.post("/protect/{item_id}/remind")
    async def protect_remind(request: Request, item_id: str) -> Response:
        form = await read_form(request)
        chosen = first(form, "days")
        with changing(state) as profile:
            if chosen == "off":
                protect.clear_reminder(profile, item_id)
            else:
                protect.set_reminder(profile, item_id, _days(chosen), views.today_for(profile))
        return redirect(f"/protect#{item_id}")

    @app.post("/alerts/{alert_id}/dismiss")
    async def alert_dismiss(request: Request, alert_id: int) -> Response:
        form = await read_form(request)
        with changing(state) as profile:
            alerts.dismiss(profile, alert_id)
        return redirect(_back(first(form, "next", "/")))
