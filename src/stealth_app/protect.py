"""The "Protect yourself" hub and the alerts every page can show."""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from stealth import alerts
from stealth.core.errors import ProfileError
from stealth.protect import checklist
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
            )

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
                try:
                    days = int(chosen)
                except ValueError:
                    raise ProfileError("Choose when to be reminded from the list.") from None
                protect.set_reminder(profile, item_id, days, views.today_for(profile))
        return redirect(f"/protect#{item_id}")

    @app.post("/alerts/{alert_id}/dismiss")
    async def alert_dismiss(request: Request, alert_id: int) -> Response:
        form = await read_form(request)
        with changing(state) as profile:
            alerts.dismiss(profile, alert_id)
        return redirect(_back(first(form, "next", "/")))
