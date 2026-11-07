"""Pages for finding more of the person: "Add a company" and the assisted scan."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote, urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from stealth.cases import engine
from stealth.cases.state import all_cases
from stealth.core.errors import ProfileError, StealthError
from stealth.custom import store as custom
from stealth.custom.email_extract import registrable
from stealth.custom.policy_fetch import look_up, normalise_domain
from stealth.profiles.store import Profile
from stealth.registry.loader import Broker
from stealth.scan import results as scans
from stealth_app import views
from stealth_app.dashboard import (
    State,
    changing,
    first,
    opened,
    read_form,
    redirect,
    render,
    start_job,
)
from stealth_app.workspace import Job

T = TypeVar("T")


def _known_company(state: State, domain: str) -> str | None:
    """A company already on Stealth's list with this website, so nobody writes to it twice."""
    own = registrable(domain)
    for broker in state.workspace.brokers().values():
        if registrable(broker.domain) == own:
            return broker.name
    return None


def _search_anywhere(profile: Profile) -> str:
    person = profile.person()
    town = next((a.split(",")[-1].strip() for a in person.addresses if "," in a), "")
    query = f'"{person.display_name}" {town}'.strip()
    return "https://duckduckgo.com/?" + urlencode({"q": query})


def install(app: FastAPI, state: State) -> None:
    # Add a company ----------------------------------------------------------------------------

    @app.get("/companies/add", response_class=HTMLResponse)
    def add_company(request: Request, site: str = "", listing: str = "") -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            return render(
                request,
                "add_company.html",
                profile=profile,
                title="Add a company",
                site=site,
                listing=listing,
                categories=custom.CATEGORIES,
                companies=custom.all_companies(profile),
            )

    @app.post("/companies/add")
    async def add_company_lookup(request: Request) -> Response:
        form = await read_form(request)
        domain = normalise_domain(first(form, "site"))
        listing = first(form, "listing").strip() or None
        category = first(form, "category", "other")
        known = _known_company(state, domain)
        if known:
            raise ProfileError(
                f"{known} is already on Stealth's list.",
                hint="Find it under Companies. Stealth writes to it already.",
            )

        def work(profile: Profile, job: Job) -> None:
            job.lines.append(f"Reading {domain}'s website for its privacy policy...")
            found = look_up(domain, fetch=state.workspace.fetch)
            company = custom.save_lookup(profile, found, listing_url=listing, category=category)
            job.next_url = f"/companies/add/{quote(company.id)}"
            job.lines.append(f"Checked {len(found.pages_checked)} page(s) on {domain}.")
            if found.blocked:
                job.lines.append(
                    f"{len(found.blocked)} page(s) skipped: the site asks programs not to "
                    "read them."
                )
            if found.best:
                job.lines.append(f"The likeliest privacy address is {found.best.address}.")
            else:
                job.lines.append("No email address was found. You can type one in.")

        job = start_job(state, f"Looking up {domain}", work, "/companies/add")
        return redirect(f"/jobs/{job.id}")

    @app.get("/companies/add/{company_id}", response_class=HTMLResponse)
    def choose_address(request: Request, company_id: str) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            company = custom.get(profile, company_id)
            return render(
                request,
                "choose_address.html",
                profile=profile,
                title=f"Add {company.name}",
                company=company,
                categories=custom.CATEGORIES,
            )

    @app.post("/companies/add/{company_id}")
    async def save_address(request: Request, company_id: str) -> Response:
        form = await read_form(request)
        picked = first(form, "email")
        if picked == "other":
            picked = first(form, "other_email")
        if not picked.strip():
            raise ProfileError("Choose an address, or type one in.")
        with changing(state) as profile:
            custom.choose(
                profile,
                company_id,
                email=picked,
                name=first(form, "name") or None,
                category=first(form, "category") or None,
                listing_url=first(form, "listing") or None,
            )
            engine.open_cases(profile, state.workspace.brokers(), hold=True)
        return redirect(f"/cases/{quote(company_id)}")

    @app.post("/companies/add/{company_id}/forget")
    async def forget_company(request: Request, company_id: str) -> Response:
        await read_form(request)
        with changing(state) as profile:
            case = next((c for c in all_cases(profile.conn) if c.broker_id == company_id), None)
            if case is not None:
                if case.status.value != "draft":
                    raise ProfileError(
                        "Stealth has already written to them, so their record stays."
                    )
                engine.skip(profile, company_id)
            custom.forget(profile, company_id)
        return redirect("/companies/add")

    # The assisted scan -------------------------------------------------------------------------

    @app.get("/scan", response_class=HTMLResponse)
    def scan(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            sites = state.workspace.scan_sites()
            all_results = scans.all_results(profile)
            return render(
                request,
                "scan.html",
                profile=profile,
                title="Scan",
                sites=[(site, scans.status_of(profile, site.broker_id)) for site in sites],
                pending=[r for r in all_results if r.outcome == scans.PENDING],
                decided=[r for r in all_results if r.outcome != scans.PENDING][:20],
                score=views.exposure(profile),
                brokers=state.workspace.brokers(),
                opened=bool(request.query_params.get("opened")),
            )

    @app.post("/scan/open")
    async def scan_open(request: Request) -> Response:
        form = await read_form(request)
        chosen = first(form, "site")
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            sites = {s.broker_id: s for s in state.workspace.scan_sites()}
            if chosen in sites:
                url = sites[chosen].start_url
            elif chosen == "web" and not state.workspace.demo:
                url = _search_anywhere(profile)
            else:
                raise ProfileError("Choose a site from the list.")
        await _in_thread(state.workspace.search_window().open, url)
        return redirect("/scan?opened=1")

    @app.post("/scan/capture")
    async def scan_capture(request: Request) -> Response:
        await read_form(request)
        capture = await _in_thread(state.workspace.search_window().capture)
        brokers = _all_brokers(state)
        with changing(state) as profile:
            brokers = custom.with_custom(profile, brokers)
            company = scans.company_for(capture.url, brokers, state.workspace.scan_sites())
            result = scans.record(profile, capture, company)
        return redirect(f"/scan/{result.id}")

    @app.get("/scan/{result_id}", response_class=HTMLResponse)
    def scan_result(request: Request, result_id: int) -> Response:
        with opened(state) as profile:
            if profile is None:
                return redirect("/")
            result = scans.get(profile, result_id)
            brokers = custom.with_custom(profile, _all_brokers(state))
            broker = brokers.get(result.broker_id or "")
            case = next(
                (c for c in all_cases(profile.conn) if c.broker_id == result.broker_id), None
            )
            host = (urlsplit(result.url).hostname or "").removeprefix("www.")
            return render(
                request,
                "scan_result.html",
                profile=profile,
                title="Capture",
                result=result,
                broker=broker,
                case=case,
                add_url="/companies/add?" + urlencode({"site": host, "listing": result.url}),
            )

    @app.get("/scan/{result_id}/image")
    def scan_image(result_id: int) -> Response:
        with opened(state) as profile:
            if profile is None:
                raise ProfileError("There's no profile yet.")
            result = scans.get(profile, result_id)
            path = views.evidence_file(profile, scans.image_path(profile, result))
            return Response(Path(path).read_bytes(), media_type="image/png")

    @app.post("/scan/{result_id}")
    async def scan_decide(request: Request, result_id: int) -> Response:
        form = await read_form(request)
        with changing(state) as profile:
            brokers = custom.with_custom(profile, _all_brokers(state))
            scans.decide(profile, result_id, first(form, "outcome"), brokers)
        return redirect(f"/scan/{result_id}")


def _all_brokers(state: State) -> dict[str, Broker]:
    return dict(state.workspace.brokers())


async def _in_thread(function: Callable[..., T], *args: object) -> T:
    """The search window can take a while to start; keep the server answering meanwhile."""
    try:
        return await asyncio.to_thread(function, *args)
    except StealthError:
        raise
    except Exception as exc:  # the browser was closed mid-way, or failed to start
        raise StealthError(
            "The search window stopped responding.",
            hint="Close it if it's still open, then open it again from the Scan page.",
        ) from exc
