"""The "Something's gone wrong" pages: the recovery playbook and its incident logs."""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from stealth.core.errors import ProfileError
from stealth.protect import incidents, recovery
from stealth.templates.render import format_date
from stealth_app import views
from stealth_app.dashboard import (
    State,
    attachment,
    changing,
    first,
    not_set_up,
    opened,
    read_form,
    redirect,
    render,
)


def _date(text: str, what: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise ProfileError(f"Enter the {what} as shown.") from None


def install(app: FastAPI, state: State) -> None:
    @app.get("/recovery", response_class=HTMLResponse)
    def recovery_page(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return not_set_up(request)
            return render(
                request,
                "recovery.html",
                profile=profile,
                title="Something's gone wrong",
                situations=recovery.SITUATIONS,
                incidents=incidents.all_incidents(profile),
                today=views.today_for(profile),
            )

    @app.post("/recovery/start")
    async def recovery_start(request: Request) -> Response:
        form = await read_form(request)
        started = _date(first(form, "started"), "date it happened")
        with changing(state) as profile:
            incident_id = incidents.start(
                profile, first(form, "situation"), started, views.today_for(profile)
            )
        return redirect(f"/recovery/{incident_id}")

    # Registered before the page, so "3.pdf" isn't read as an incident number.
    @app.get("/recovery/{incident_id}.pdf")
    def incident_pdf(incident_id: int) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            incident = incidents.get(profile, incident_id)
            person = profile.person().display_name
            today = views.today_for(profile)
        from stealth_app import recovery_pdf  # ReportLab loads only when asked for

        name = f"stealth-incident-{incident.started_on.isoformat()}-{incident.situation.id}.pdf"
        return Response(
            recovery_pdf.render(incident, person, today),
            media_type="application/pdf",
            headers={"Content-Disposition": attachment(name)},
        )

    @app.get("/recovery/{incident_id}", response_class=HTMLResponse)
    def incident_page(request: Request, incident_id: int) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/recovery")
            incident = incidents.get(profile, incident_id)
            return render(
                request,
                "incident.html",
                profile=profile,
                title=incident.situation.title,
                incident=incident,
                today=views.today_for(profile),
                checked_on=format_date(date.fromisoformat(recovery.CHECKED_ON)),
                reference_limit=incidents.REFERENCE_LIMIT,
                note_limit=incidents.NOTE_LIMIT,
            )

    @app.post("/recovery/{incident_id}/action/{action_id}")
    async def incident_action(request: Request, incident_id: int, action_id: str) -> Response:
        form = await read_form(request)
        with changing(state) as profile:
            incidents.save_action(
                profile,
                incident_id,
                action_id,
                done=first(form, "done") == "yes",
                reference=first(form, "reference"),
                note=first(form, "note"),
                today=views.today_for(profile),
            )
        return redirect(f"/recovery/{incident_id}#action-{action_id}")

    @app.post("/recovery/{incident_id}/log")
    async def incident_log(request: Request, incident_id: int) -> Response:
        form = await read_form(request)
        when = _date(first(form, "date"), "date")
        with changing(state) as profile:
            incidents.add_entry(
                profile,
                incident_id,
                when,
                first(form, "text"),
                first(form, "reference"),
                views.today_for(profile),
            )
        return redirect(f"/recovery/{incident_id}#log")

    @app.post("/recovery/{incident_id}/log/{entry_id}/delete")
    async def incident_log_delete(request: Request, incident_id: int, entry_id: int) -> Response:
        await read_form(request)
        with changing(state) as profile:
            incidents.delete_entry(profile, incident_id, entry_id)
        return redirect(f"/recovery/{incident_id}#log")

    @app.post("/recovery/{incident_id}/close")
    async def incident_close(request: Request, incident_id: int) -> Response:
        form = await read_form(request)
        with changing(state) as profile:
            incidents.close(
                profile,
                incident_id,
                views.today_for(profile),
                closed=first(form, "closed") == "yes",
            )
        return redirect(f"/recovery/{incident_id}")

    @app.post("/recovery/{incident_id}/delete")
    async def incident_delete(request: Request, incident_id: int) -> Response:
        form = await read_form(request)
        if first(form, "confirm") != "yes":
            raise ProfileError("Tick the box to confirm you want to delete this log.")
        with changing(state) as profile:
            incidents.delete(profile, incident_id)
        return redirect("/recovery")
