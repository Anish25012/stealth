"""The dashboard's pages and the actions behind its buttons.

Plain HTML forms, no JavaScript framework: every action is a POST carrying a CSRF token, answered
with a redirect, so the back button and keyboard work as people expect. Anything slow (a cycle,
a batch of sends) runs as a background job with a progress page that refreshes itself.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.responses import Response

from stealth import __version__, cycle
from stealth.cases import engine
from stealth.cases.state import Status, find_case
from stealth.core.errors import ProfileError, StealthError
from stealth.core.logging_scrub import add_known_values
from stealth.inbox import responses
from stealth.profiles.store import Profile
from stealth.templates.render import format_date
from stealth_app import autostart, monthly, updates, views
from stealth_app.workspace import DemoWorkspace, Job, Jobs, RealWorkspace, Workspace

CSRF_FIELD = "csrf"

TEMPLATES = Environment(
    loader=PackageLoader("stealth_app", "templates"),
    autoescape=select_autoescape(["html"], default=True),
    trim_blocks=True,
    lstrip_blocks=True,
)
TEMPLATES.filters["day"] = lambda d: format_date(d) if d else ""
TEMPLATES.filters["when"] = lambda dt: f"{format_date(dt.date())}, {dt:%H:%M} UTC"


class CsrfError(StealthError):
    """A form arrived without this launch's token: a stale page or another site's form."""


@dataclass
class State:
    """Everything the pages share for the life of one server."""

    real: Workspace
    csrf: str
    jobs: Jobs
    demo: DemoWorkspace | None = None
    phone: Any = None  # a running phone.PhoneView, if phone view is on

    @property
    def workspace(self) -> Workspace:
        return self.demo or self.real


def render(
    request: Request,
    name: str,
    status_code: int = 200,
    profile: Profile | None = None,
    **context: Any,
) -> HTMLResponse:
    """Render a page. Given the open profile, the sidebar shows how many things need the person."""
    state: State = request.app.state.dashboard
    if profile is not None and "nav_counts" not in context:
        context["nav_counts"] = views.nav_counts(views.grouped(views.rows(profile)))
    page = TEMPLATES.get_template(name).render(
        csrf=state.csrf,
        demo=state.workspace.demo,
        version=__version__,
        path=request.url.path,
        **context,
    )
    return HTMLResponse(page, status_code=status_code)


# The sidebar's pages, for before there's a profile: say why they're empty instead of bouncing
# back to the start, which looked like the click did nothing.
EMPTY_PAGES: dict[str, tuple[str, str, str]] = {
    "/cases": (
        "Companies",
        "Every company Stealth writes to, and where each one stands.",
        "building-2",
    ),
    "/scan": ("Scan", "See what people-search sites show about you.", "search"),
    "/needs": ("Needs you", "Replies to read and letters to approve.", "inbox"),
    "/approve": ("Waiting for your OK", "Letters Stealth has written for you to read.", "mail"),
    "/reports": ("Reports", "Monthly reports, your exposure score and evidence.", "file-text"),
}


def not_set_up(request: Request) -> HTMLResponse:
    title, about, glyph = EMPTY_PAGES[request.url.path]
    return render(request, "not_set_up.html", title=title, about=about, glyph=glyph)


STATIC_TYPES = {
    ".css": "text/css",
    ".js": "text/javascript",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def static_response(name: str) -> Response:
    """One file from the static folder. Names only, never paths; dot-files are refused."""
    entry = resources.files("stealth_app") / "static" / name
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if name.startswith(".") or suffix not in STATIC_TYPES or not entry.is_file():
        return PlainTextResponse("Not found.", status_code=404)
    return Response(entry.read_bytes(), media_type=STATIC_TYPES[suffix])


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


async def read_form(request: Request) -> dict[str, list[str]]:
    """Parse a posted form and check its CSRF token. Every state-changing route calls this."""
    state: State = request.app.state.dashboard
    form = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
    sent = form.get(CSRF_FIELD, [""])[0]
    if not secrets.compare_digest(sent, state.csrf):
        raise CsrfError(
            "This page was out of date, so nothing was changed.",
            hint="Go back, refresh the page and try again.",
        )
    return form


def first(form: dict[str, list[str]], name: str, default: str = "") -> str:
    return form.get(name, [default])[0]


@contextmanager
def opened(state: State) -> Iterator[Profile | None]:
    """The active profile of the current workspace, or None if there isn't one yet."""
    store = state.workspace.store
    if not store.list_ids():
        yield None
        return
    with store.open() as profile:
        person = profile.person()
        # Anything about this person that reaches a log or a copied error report gets redacted.
        add_known_values([*person.names, *person.addresses, person.display_name])
        yield profile


@contextmanager
def changing(state: State) -> Iterator[Profile]:
    """A profile opened for a change, with the workspace lock held."""
    if not state.workspace.lock.acquire(blocking=False):
        raise StealthError(
            "Stealth is busy with something else right now.",
            hint="Wait for it to finish, then try again.",
        )
    try:
        with opened(state) as profile:
            if profile is None:
                raise ProfileError("There's no profile yet.")
            yield profile
    finally:
        state.workspace.lock.release()


def start_job(state: State, title: str, work: Callable[[Profile, Job], None], next_url: str) -> Job:
    def run(job: Job) -> None:
        with opened(state) as profile:
            if profile is None:
                raise ProfileError("There's no profile yet.")
            work(profile, job)

    return state.jobs.start(state.workspace, title, run, next_url=next_url)


def _names(items: list[str]) -> str:
    return ", ".join(items)


def install(app: FastAPI, real: Workspace | None = None) -> State:
    state = State(real=real or RealWorkspace(), csrf=secrets.token_urlsafe(32), jobs=Jobs())
    app.state.dashboard = state

    # Pages ----------------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return render(request, "welcome.html", title="Stealth")
            rows = views.rows(profile)
            groups = views.grouped(rows)
            return render(
                request,
                "overview.html",
                title="Stealth",
                person=profile.person(),
                groups=groups,
                summary=views.summary(groups),
                nav_counts=views.nav_counts(groups),
                group_text=views.GROUPS,
                total=len(rows),
                running=state.jobs.running(),
                settings=profile.settings(),
                last_run=views.last_run(profile.settings()),
                score=views.exposure(profile),
            )

    @app.get("/cases", response_class=HTMLResponse)
    def case_list(request: Request, group: str = "") -> Response:
        with opened(state) as profile:
            if profile is None:
                return not_set_up(request)
            rows = views.rows(profile)
            if group in views.GROUPS:
                rows = [r for r in rows if r.info.group == group]
            return render(
                request,
                "cases.html",
                profile=profile,
                title="Companies",
                rows=rows,
                group=group if group in views.GROUPS else "",
                group_text=views.GROUPS,
            )

    @app.get("/cases/{broker_id}", response_class=HTMLResponse)
    def case_detail(request: Request, broker_id: str) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            case = _case(profile, broker_id)
            broker = state.workspace.brokers().get(case.broker_id)
            asked = views.can_respond(profile, case)
            draft = responses.response_message(profile, case, asked) if asked else None
            return render(
                request,
                "case.html",
                profile=profile,
                title=case.broker_name,
                case=case,
                info=views.STATUS[case.status],
                explanation=views.explain(case, views.today_for(profile)),
                timeline=views.timeline(profile, case),
                outcomes=views.outcomes_for(case),
                reply=views.latest_reply(profile, case),
                suggestion=views.suggestion(
                    profile, case, set(broker.all_domains) if broker else set()
                ),
                asked=asked,
                draft=draft,
                can_skip=views.can_skip(case),
                can_complain=views.can_complain(case),
                can_ico=case.status in (Status.ICO_READY, Status.ESCALATED),
                form_url=broker.form_url if broker else None,
                dead_contact=views.dead_contact_link(profile, case),
            )

    @app.get("/cases/{broker_id}/messages/{message_id}", response_class=HTMLResponse)
    def message(request: Request, broker_id: str, message_id: int) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            case = _case(profile, broker_id)
            return render(
                request,
                "message.html",
                profile=profile,
                title=f"Email: {case.broker_name}",
                case=case,
                message=views.readable(profile, case, message_id),
            )

    @app.get("/cases/{broker_id}/evidence.zip")
    def evidence(broker_id: str) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            case = _case(profile, broker_id)
            return Response(
                views.evidence_pack(profile, case),
                media_type="application/zip",
                headers={"Content-Disposition": _attachment(f"stealth-{case.broker_id}.zip")},
            )

    @app.get("/approve", response_class=HTMLResponse)
    def approve_page(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return not_set_up(request)
            drafts = engine.drafts_waiting(profile)
            letters = [(case, engine.draft_letter(profile, case)) for case in drafts]
            settings = profile.settings()
            return render(
                request,
                "approve.html",
                profile=profile,
                title="Waiting for your OK",
                letters=letters,
                account=profile.mail_account(),
                daily_limit=settings.daily_send_limit,
            )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str) -> Response:
        job = state.jobs.get(job_id)
        if job is None:
            return redirect("/")
        return render(request, "job.html", title=job.title, job=job)

    @app.get("/needs", response_class=HTMLResponse)
    def needs(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return not_set_up(request)
            groups = views.grouped(views.rows(profile))
            return render(
                request,
                "needs.html",
                title="Needs you",
                groups=groups,
                group_text=views.GROUPS,
                nav_counts=views.nav_counts(groups),
            )

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return not_set_up(request)
            rows = views.rows(profile)
            groups = views.grouped(rows)
            return render(
                request,
                "reports.html",
                title="Reports",
                groups=groups,
                group_text=views.GROUPS,
                summary=views.summary(groups),
                total=len(rows),
                ico=[r for r in rows if r.case.status in (Status.ICO_READY, Status.ESCALATED)],
                sent=[r for r in rows if r.case.last_request_on],
                score=views.exposure(profile),
                months=monthly.available(profile),
                nav_counts=views.nav_counts(groups),
            )

    # Registered before the page, so "2026-10.pdf" isn't read as a month called that.
    @app.get("/reports/monthly/{key}.pdf")
    def monthly_pdf(key: str) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            report = monthly.build(profile, monthly.Month.parse(key))
        from stealth_app import monthly_pdf as pdf  # ReportLab loads only when asked for

        return Response(
            pdf.render(report),
            media_type="application/pdf",
            headers={"Content-Disposition": _attachment(f"stealth-report-{report.month.key}.pdf")},
        )

    @app.get("/reports/monthly/{key}", response_class=HTMLResponse)
    def monthly_page(request: Request, key: str) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            report = monthly.build(profile, monthly.Month.parse(key))
            months = monthly.available(profile)
            newer = report.month.next()
            return render(
                request,
                "monthly.html",
                title=f"{report.month.label} report",
                report=report,
                older=report.month.previous() if report.month.previous() in months else None,
                newer=newer if newer in months else None,
                nav_counts=views.nav_counts(views.grouped(views.rows(profile))),
            )

    def update_context() -> dict[str, Any]:
        phone = state.phone if state.phone is not None and state.phone.active() else None
        return {
            "updates": updates.load(),
            "list_version": updates.list_version(),
            "phone": phone,
        }

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return render(request, "settings.html", title="Settings", **update_context())
            return render(
                request,
                "settings.html",
                profile=profile,
                title="Settings",
                person=profile.person(),
                account=profile.mail_account(),
                settings=profile.settings(),
                last_run=views.last_run(profile.settings()),
                autostart_available=autostart.available(),
                autostart_on=autostart.is_enabled(),
                hours=range(6, 23),
                **update_context(),
            )

    @app.post("/settings/updates")
    async def update_setting(request: Request) -> Response:
        form = await read_form(request)
        updates.set_enabled(first(form, "check_updates") == "yes")
        return redirect("/settings#updates-h")

    @app.post("/updates/check")
    async def check_now(request: Request) -> Response:
        await read_form(request)
        result = updates.check()
        if result.list_updated:
            state.real.forget_brokers()
        return redirect("/settings#updates-h")

    @app.post("/settings/automation")
    async def automation(request: Request) -> Response:
        form = await read_form(request)
        if state.workspace.demo:
            raise StealthError(
                "The demo doesn't run by itself.", hint="Leave the demo to change this."
            )
        hour = first(form, "hour", "9")
        if not hour.isdigit() or not 0 <= int(hour) <= 23:
            raise ProfileError("Choose a time from the list.")
        with changing(state) as profile:
            profile.save_settings(
                auto_run=first(form, "auto_run") == "yes", auto_run_hour=int(hour)
            )
        autostart.set_enabled(first(form, "autostart") == "yes")
        return redirect("/settings")

    @app.get("/help", response_class=HTMLResponse)
    def help_page(request: Request) -> Response:
        with opened(state) as profile:
            return render(request, "help.html", profile=profile, title="Help")

    # Actions --------------------------------------------------------------------------------

    @app.post("/cycle")
    async def run_cycle(request: Request) -> Response:
        await read_form(request)

        def work(profile: Profile, job: Job) -> None:
            transport, mailbox = state.workspace.mail(profile)
            job.lines.append("Checking your mailbox for replies...")
            report = cycle.run_cycle(
                profile,
                state.workspace.brokers(),
                transport,
                mailbox,
                sleep=state.workspace.sleep,
                hold_for_approval=True,
            )
            job.lines += _cycle_summary(report)
            waiting = len(engine.drafts_waiting(profile))
            if waiting:
                job.lines.append(
                    f"{waiting} letter(s) are waiting for your OK before they're sent."
                )
            if len(job.lines) == 1:
                job.lines.append("Nothing needed doing today.")

        return redirect(f"/jobs/{start_job(state, 'Running a cycle', work, '/').id}")

    @app.post("/approve")
    async def approve(request: Request) -> Response:
        form = await read_form(request)
        chosen = form.get("broker", [])
        action = first(form, "action", "approve")
        if not chosen:
            raise ProfileError("No letters were ticked.", hint="Tick the letters you want first.")
        if action == "skip":
            with changing(state) as profile:
                for broker_id in chosen:
                    engine.skip(profile, broker_id)
            return redirect("/approve")
        with changing(state) as profile:
            engine.approve(profile, chosen)

        def work(profile: Profile, job: Job) -> None:
            job.lines.append(f"Sending {len(chosen)} approved letter(s)...")
            transport, _mailbox = state.workspace.mail(profile)
            report = engine.send_requests(
                profile,
                state.workspace.brokers(),
                transport,
                sleep=state.workspace.sleep,
                hold_for_approval=True,
            )
            job.lines += _send_summary(report)

        return redirect(f"/jobs/{start_job(state, 'Sending your letters', work, '/').id}")

    @app.post("/cases/{broker_id}/outcome")
    async def outcome(request: Request, broker_id: str) -> Response:
        form = await read_form(request)
        choice = first(form, "outcome")
        with changing(state) as profile:
            case = _case(profile, broker_id)
            if choice not in {value for value, _label in views.outcomes_for(case)}:
                raise ProfileError("Choose one of the answers listed.")
            engine.confirm(profile, case.broker_id, choice)
        return redirect(f"/cases/{quote(broker_id)}")

    @app.post("/cases/{broker_id}/form-done")
    async def form_done(request: Request, broker_id: str) -> Response:
        await read_form(request)
        with changing(state) as profile:
            engine.form_done(profile, _case(profile, broker_id).broker_id)
        return redirect(f"/cases/{quote(broker_id)}")

    @app.post("/cases/{broker_id}/skip")
    async def skip(request: Request, broker_id: str) -> Response:
        await read_form(request)
        with changing(state) as profile:
            engine.skip(profile, _case(profile, broker_id).broker_id)
        return redirect(f"/cases/{quote(broker_id)}")

    @app.post("/cases/{broker_id}/complain")
    async def complain(request: Request, broker_id: str) -> Response:
        await read_form(request)
        target = f"/cases/{quote(broker_id)}"

        def work(profile: Profile, job: Job) -> None:
            case = _case(profile, broker_id)
            transport, _mailbox = state.workspace.mail(profile)
            engine.send_complaint(profile, case, transport, grievance=engine.REFUSED)
            job.lines.append(f"Formal complaint sent to {case.broker_name}.")

        return redirect(f"/jobs/{start_job(state, 'Sending a complaint', work, target).id}")

    @app.post("/cases/{broker_id}/respond")
    async def respond(request: Request, broker_id: str) -> Response:
        await read_form(request)
        target = f"/cases/{quote(broker_id)}"
        with opened(state) as profile:
            if profile is None or views.can_respond(profile, _case(profile, broker_id)) is None:
                raise ProfileError("There's nothing for Stealth to answer here.")

        def work(profile: Profile, job: Job) -> None:
            case = _case(profile, broker_id)
            transport, _mailbox = state.workspace.mail(profile)
            case = responses.send_response(profile, case, transport)
            job.lines.append(f"Your answer was sent to {case.broker_name}.")
            if case.deadline_on:
                job.lines.append(f"They now have until {format_date(case.deadline_on)} to act.")

        return redirect(f"/jobs/{start_job(state, 'Sending your answer', work, target).id}")

    @app.post("/cases/{broker_id}/ico")
    async def ico(request: Request, broker_id: str) -> Response:
        await read_form(request)
        with changing(state) as profile:
            case = _case(profile, broker_id)
            path = engine.draft_ico_complaint(profile, case.broker_id)
            text = path.read_text("utf-8")
        return Response(
            text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": _attachment(f"ico-complaint-{case.broker_id}.txt")},
        )

    @app.post("/demo/start")
    async def demo_start(request: Request) -> Response:
        await read_form(request)
        if state.demo is None:
            state.demo = DemoWorkspace()
        return redirect("/")

    @app.post("/demo/stop")
    async def demo_stop(request: Request) -> Response:
        await read_form(request)
        demo, state.demo = state.demo, None
        if demo is not None:
            demo.close()
        return redirect("/")

    return state


def _case(profile: Profile, broker_id: str) -> Any:
    # Exact ids only: the command line's name matching would let a URL pick a different company.
    row = profile.conn.execute("SELECT broker_id FROM cases WHERE broker_id = ?", (broker_id,))
    if row.fetchone() is None:
        raise ProfileError("Stealth has no case for that company.")
    return find_case(profile.conn, broker_id)


def _attachment(filename: str) -> str:
    return f'attachment; filename="{filename}"'


def _cycle_summary(report: cycle.CycleReport) -> list[str]:
    lines = []
    for company, meaning in report.inbox.resolved:
        lines.append(f"{company} replied: {meaning[0].lower()}{meaning[1:]}. Stealth recorded it.")
    if report.inbox.acknowledged:
        lines.append(
            f"{_names(report.inbox.acknowledged)} confirmed they got your letter. "
            "Their time limit keeps running."
        )
    if report.inbox.replies:
        lines.append(
            f"Replies from: {_names(report.inbox.replies)}. Read them and say what they mean."
        )
    if report.inbox.bounces:
        lines.append(f"Emails that didn't arrive: {_names(report.inbox.bounces)}.")
    if report.complaints:
        lines.append(f"Formal complaints sent to: {_names(report.complaints)}.")
    if report.complaints_held:
        lines.append(f"{report.complaints_held} complaint(s) held back by your daily limit.")
    if report.ico_ready:
        lines.append(f"Ready for the ICO: {_names(report.ico_ready)}.")
    if report.needs_you:
        lines.append(f"Need you: {_names(report.needs_you)}.")
    if report.resends:
        lines.append(f"Due a repeat request: {_names(report.resends)}.")
    lines += _send_summary(report.send)
    return lines


def _send_summary(report: engine.SendReport) -> list[str]:
    lines = []
    if report.sent:
        lines.append(f"Letters sent to: {_names(report.sent)}.")
    if report.failed:
        lines.append(f"Couldn't send to: {_names(report.failed)}. Stealth will try again.")
    if report.held_back:
        lines.append(
            f"{report.held_back} letter(s) wait for tomorrow, to stay within your daily limit."
        )
    if report.forms:
        lines.append(f"Web forms to fill in: {_names(report.forms)}.")
    return lines
