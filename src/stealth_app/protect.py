"""The "Protect yourself" hub and the alerts every page can show."""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from stealth import alerts
from stealth.core.errors import ProfileError
from stealth.protect import checklist, cifas, credit
from stealth.protect import store as protect
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
)


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
            )

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
