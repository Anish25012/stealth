"""The `stealth` command. Only prompts and printing live here; all logic is in the engine."""

from __future__ import annotations

import csv
import dataclasses
import functools
import logging
import re
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any, ParamSpec, TypeVar

import typer

from stealth import __version__, checklist, cycle
from stealth.cases import engine
from stealth.cases.state import Status, all_cases
from stealth.core import paths
from stealth.core.errors import StealthError
from stealth.core.jurisdictions import for_country
from stealth.core.logging_scrub import setup_logging
from stealth.core.secrets import KeyringStore, SecretStore
from stealth.demo import run_demo
from stealth.mail.factory import open_mail
from stealth.mail.smtp_imap import check_connection
from stealth.profiles.models import GMAIL_DEFAULTS, MailAccount, Person
from stealth.profiles.store import Profile, ProfileStore
from stealth.registry.loader import Broker, counts, load_registry, select_brokers

app = typer.Typer(
    help="Stealth: remove your personal data from data brokers, from your own computer.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
log = logging.getLogger("stealth.cli")

P = ParamSpec("P")
R = TypeVar("R")
ProfileOption = Annotated[str | None, typer.Option(help="Profile id, if you have more than one.")]
LiveOption = Annotated[bool, typer.Option("--live", help="Really send. Without it, only preview.")]


def _secrets() -> SecretStore:
    return KeyringStore()


def _store() -> ProfileStore:
    return ProfileStore(_secrets())


@contextmanager
def _profile(profile_id: str | None) -> Iterator[Profile]:
    with _store().open(profile_id) as profile:
        yield profile


def _brokers() -> dict[str, Broker]:
    return load_registry()


def guarded(function: Callable[P, R]) -> Callable[P, R]:
    """Show plain English for failures we expect, and never a traceback for ones we don't."""

    @functools.wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except StealthError as exc:
            typer.secho(exc.message, fg=typer.colors.RED, err=True)
            if exc.hint:
                typer.echo(exc.hint, err=True)
            raise typer.Exit(1) from exc
        except (typer.Exit, typer.Abort):
            raise
        except Exception as exc:
            log.exception("unexpected error")
            typer.secho("Something went wrong inside Stealth.", fg=typer.colors.RED, err=True)
            typer.echo(f"Details were saved to the log in {paths.logs_dir()}", err=True)
            raise typer.Exit(2) from exc

    return wrapper


def _show_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def _startup(
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the version.", callback=_show_version, is_eager=True),
    ] = False,
) -> None:
    setup_logging(paths.logs_dir())


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ask_email(label: str, default: str | None = None) -> str:
    while True:
        value = str(typer.prompt(label, default=default)).strip()
        if EMAIL_RE.match(value):
            return value
        typer.secho(f"{value!r} isn't an email address. Try again.", fg="red")


def _ask_password(account: MailAccount) -> str:
    """An app password the provider accepts, or one the person chose to save unchecked.

    Proving it works here, before anything is saved, is what the desktop app does too. Without
    it, a mistyped password is only discovered when the first letter fails to send.
    """
    while True:
        password = typer.prompt(
            "App password (stored in your computer's keychain)", hide_input=True
        )
        password = str(password).replace(" ", "")  # Google shows app passwords in groups of four
        if account.smtp_host == GMAIL_DEFAULTS["smtp_host"] and len(password) != 16:
            typer.secho(
                f"That's {len(password)} characters, but a Gmail app password is 16 letters. "
                "Make one at https://myaccount.google.com/apppasswords",
                fg="red",
            )
            continue
        typer.echo("Checking Stealth can send and read mail...")
        try:
            check_connection(account, password)
        except StealthError as exc:
            typer.secho(exc.message, fg="red")
            if exc.hint:
                typer.echo(exc.hint)
            if typer.confirm("Save it anyway and fix this later?", default=False):
                return password
            continue
        typer.secho("It works.", fg="green")
        return password


def _ask_list(label: str, first: str | None = None) -> list[str]:
    """Ask for any number of values, one per line, ending with a blank."""
    items = [first] if first else []
    while True:
        value = typer.prompt(f"{label} (press Enter to finish)", default="", show_default=False)
        if not value.strip():
            return items
        items.append(value.strip())


@app.command()
@guarded
def init() -> None:
    """Set up a new profile: your details, email account and sending preferences."""
    typer.echo("Stealth keeps everything on this computer. Let's set up your profile.\n")
    name = typer.prompt("Your full name")
    names = _ask_list("Any other names you've used", first=name)
    email_address = _ask_email("Your main email address")
    emails = _ask_list("Any other email addresses", first=email_address)
    addresses = _ask_list("Home address, past or present")
    phones = _ask_list("Phone number")
    for_someone_else = typer.confirm("Are you setting this up for someone else?", default=False)

    typer.echo("\nStealth sends from your own mailbox. These defaults are for Gmail.")
    typer.echo("Gmail needs an app password: https://myaccount.google.com/apppasswords\n")
    sender = _ask_email("Send from this address", default=email_address)
    smtp_host = typer.prompt("SMTP server", default=GMAIL_DEFAULTS["smtp_host"])
    smtp_port = typer.prompt("SMTP port", default=GMAIL_DEFAULTS["smtp_port"], type=int)
    imap_host = typer.prompt("IMAP server", default=GMAIL_DEFAULTS["imap_host"])
    imap_port = typer.prompt("IMAP port", default=GMAIL_DEFAULTS["imap_port"], type=int)
    account = MailAccount(
        provider="smtp",
        address=sender,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_security="ssl" if smtp_port == 465 else "starttls",
        imap_host=imap_host,
        imap_port=imap_port,
        imap_security="ssl",
        username=sender,
        is_own_mailbox=not for_someone_else,
    )
    password = _ask_password(account)

    typer.echo("")
    people_search = typer.prompt("Days before repeating people-search requests", default=60)
    other = typer.prompt("Days before repeating other requests", default=90)
    delay = typer.prompt("Seconds between emails", default=20)
    limit = typer.prompt("Most emails to send in a day", default=40)
    skip = typer.prompt("Broker ids to skip, comma separated", default="", show_default=False)

    store = _store()
    profile_id, recovery_code = store.create()
    with store.open(profile_id) as profile:
        profile.save_person(
            Person(
                display_name=name,
                names=names,
                emails=emails,
                addresses=addresses,
                phones=phones,
            )
        )
        profile.save_mail_account(account, password=password)
        profile.save_settings(
            resend_days_people_search=int(people_search),
            resend_days_other=int(other),
            send_delay_seconds=int(delay),
            daily_send_limit=int(limit),
            skip_brokers=[s.strip() for s in skip.split(",") if s.strip()],
        )
        if for_someone_else:
            given_by = typer.prompt("Who agreed to this? (their name)", default=name)
            method = typer.prompt("How did they agree? (for example: in person)")
            profile.record_consent(name, given_by=given_by, method=method)

    typer.echo(f"\nProfile created: {profile_id}")
    typer.secho(f"Your recovery code: {recovery_code}", bold=True)
    typer.echo("Write this down and keep it safe. It's the only way back in if this computer's")
    typer.echo("keychain is lost, and Stealth can't show it again.")
    typer.echo("\nNext: `stealth send` to preview your letters.")


@app.command()
@guarded
def password(profile_id: ProfileOption = None, address: str | None = None) -> None:
    """Replace the app password (and optionally the sending address), checking it works first."""
    with _profile(profile_id) as profile:
        account = profile.mail_account()
        if account is None:
            raise StealthError("This profile has no email account yet.", hint="Run `stealth init`.")
        if address:
            if not EMAIL_RE.match(address):
                raise StealthError(f"{address!r} isn't an email address.")
            account = dataclasses.replace(account, address=address, username=address)
        typer.echo(f"Sending from: {account.address}")
        new = _ask_password(account)
        profile.save_mail_account(account, password=new)
    typer.echo("Saved.")


def _print_preview(report: engine.SendReport, profile: Profile) -> None:
    typer.echo(f"Preview: {len(report.written)} letters written to {profile.outbox}")
    if report.forms:
        typer.echo(f"{len(report.forms)} companies use web forms: see `stealth forms`.")


def _confirm_send(brokers: dict[str, Broker], profile: Profile) -> None:
    jurisdiction = engine.jurisdiction_of(profile)
    settings = profile.settings()
    due = engine.due_selections(profile, select_brokers(brokers, jurisdiction, settings))
    emails = [s for s in due if s.broker.method == "email"]
    account = profile.mail_account()
    assert account
    typer.echo(f"From: {account.address}")
    typer.echo(f"Companies due a letter: {len(emails)} by email, {len(due) - len(emails)} by form")
    limit = settings.daily_send_limit - engine.sent_today(profile, jurisdiction)
    typer.echo(f"Sending up to {max(0, limit)} today, {settings.send_delay_seconds}s apart.")
    typer.echo(f"The rest go on later days. A copy of each is BCCed to {account.address}.")
    if not typer.confirm("Send these now?", default=False):
        raise typer.Abort


def _only(brokers: dict[str, Broker], broker_id: str, profile: Profile) -> dict[str, Broker]:
    """Narrow the list to one broker, refusing anything that would quietly send nothing."""
    if broker_id not in brokers:
        raise StealthError(f"There's no broker with the id {broker_id}.")
    narrowed = {broker_id: brokers[broker_id]}
    jurisdiction = engine.jurisdiction_of(profile)
    if not select_brokers(narrowed, jurisdiction, profile.settings()):
        raise StealthError(
            f"{narrowed[broker_id].name} isn't on your list.",
            hint="It may be an optional company, or on your skip list.",
        )
    return narrowed


@app.command()
@guarded
def send(
    live: LiveOption = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation.")] = False,
    only: Annotated[str | None, typer.Option(help="Send to this one broker id only.")] = None,
    profile_id: ProfileOption = None,
) -> None:
    """Preview your letters, or send them with --live."""
    brokers = _brokers()
    with _profile(profile_id) as profile:
        if only:
            brokers = _only(brokers, only, profile)
        if not live:
            _print_preview(engine.preview_requests(profile, brokers), profile)
            typer.echo("Nothing was sent. Add --live to send for real.")
            return
        if not yes:
            _confirm_send(brokers, profile)
        transport, _ = open_mail(profile)
        report = engine.send_requests(profile, brokers, transport, sleep=time.sleep, only=only)
        typer.echo(f"Sent {len(report.sent)}.")
        if report.held_back:
            typer.echo(f"{report.held_back} held back by your daily limit, for later runs.")
        if report.failed:
            typer.secho(f"Failed: {', '.join(report.failed)}", fg=typer.colors.YELLOW)


@app.command()
@guarded
def inbox(profile_id: ProfileOption = None) -> None:
    """Check your mailbox for replies. Your mailbox is only ever read, never changed."""
    from stealth.inbox.processor import process_inbox

    with _profile(profile_id) as profile:
        _, mailbox = open_mail(profile)
        report = process_inbox(profile, mailbox, _brokers())
    typer.echo(f"{len(report.replies)} replies, {len(report.bounces)} bounces.")
    for name in report.replies:
        typer.echo(f"  Reply from {name}: read it, then run `stealth confirm`.")
    for name in report.bounces:
        typer.secho(f"  {name}: the address bounced.", fg=typer.colors.YELLOW)


@app.command()
@guarded
def confirm(
    broker: str,
    outcome: Annotated[
        str,
        typer.Argument(
            help="removed, no_data, refused, id_requested, extended, info_supplied or escalated"
        ),
    ],
    profile_id: ProfileOption = None,
) -> None:
    """Record what a company's reply meant."""
    with _profile(profile_id) as profile:
        case = engine.confirm(profile, broker, outcome)
    typer.echo(f"{case.broker_name}: now {case.status.value}.")


@app.command()
@guarded
def complain(broker: str, profile_id: ProfileOption = None) -> None:
    """Send a formal complaint now, for a company that has refused."""
    from stealth.cases.state import find_case

    with _profile(profile_id) as profile:
        case = find_case(profile.conn, broker)
        transport, _ = open_mail(profile)
        grievance = engine.REFUSED if case.status is Status.REFUSED else engine.NO_REPLY
        updated = engine.send_complaint(profile, case, transport, grievance=grievance)
    typer.echo(f"Complaint sent to {updated.broker_name}.")


@app.command()
@guarded
def ico(broker: str, profile_id: ProfileOption = None) -> None:
    """Draft a complaint to the ICO about a company."""
    with _profile(profile_id) as profile:
        path = engine.draft_ico_complaint(profile, broker)
    typer.echo(f"Draft written to {path}")
    typer.echo("Once you've submitted it: stealth confirm <broker> escalated")


@app.command("cycle")
@guarded
def cycle_command(live: LiveOption = False, profile_id: ProfileOption = None) -> None:
    """Do everything due: replies, follow-ups, new and repeat requests. For scheduling."""
    brokers = _brokers()
    with _profile(profile_id) as profile:
        if not live:
            preview = cycle.preview_cycle(profile, brokers)
            _print_preview(preview.send, profile)
            typer.echo(f"{len(preview.complaints)} formal complaints are due.")
            typer.echo("Nothing was sent. Add --live to send for real.")
            return
        transport, mailbox = open_mail(profile)
        report = cycle.run_cycle(profile, brokers, transport, mailbox)
    typer.echo(f"Replies: {len(report.inbox.replies)}  Bounces: {len(report.inbox.bounces)}")
    typer.echo(f"Complaints sent: {len(report.complaints)}  ICO-ready: {len(report.ico_ready)}")
    typer.echo(f"Requests sent: {len(report.send.sent)}  Repeats due: {len(report.resends)}")
    if report.send.held_back or report.complaints_held:
        waiting = report.send.held_back + report.complaints_held
        typer.echo(f"{waiting} held back by your daily limit, for later runs.")
    if report.needs_you:
        typer.secho(f"Needs you: {', '.join(report.needs_you)}", fg=typer.colors.YELLOW)


@app.command()
@guarded
def forms(
    done: Annotated[str | None, typer.Option(help="Broker id whose form you've sent.")] = None,
    profile_id: ProfileOption = None,
) -> None:
    """List companies that need a web form, and record when you've done one."""
    brokers = _brokers()
    with _profile(profile_id) as profile:
        if done:
            case = engine.form_done(profile, done)
            typer.echo(f"{case.broker_name}: clock started, reply due {case.deadline_on}.")
            return
        jurisdiction = engine.jurisdiction_of(profile)
        started = {c.broker_id for c in all_cases(profile.conn)}
        waiting = {c.broker_id for c in all_cases(profile.conn) if c.status is Status.AWAITING_FORM}
        chosen = select_brokers(brokers, jurisdiction, profile.settings())
    rows = [
        s.broker
        for s in chosen
        if s.broker.method == "form" and (s.broker.id in waiting or s.broker.id not in started)
    ]
    if not rows:
        typer.echo("No web forms waiting.")
    for broker in rows:
        typer.echo(f"{broker.id}: {broker.name}\n  {broker.form_url}")
        if broker.notes:
            typer.echo(f"  {broker.notes}")


STATUS_COLUMNS = ("broker", "status", "sent", "deadline", "next_action", "reference")


def _status_rows(profile: Profile) -> list[tuple[Any, ...]]:
    return [
        (
            c.broker_name,
            c.status.value,
            c.last_request_on or "",
            c.deadline_on or "",
            c.next_action_on or "",
            c.ref,
        )
        for c in all_cases(profile.conn)
    ]


@app.command()
@guarded
def status(
    as_csv: Annotated[bool, typer.Option("--csv", help="Output CSV.")] = False,
    profile_id: ProfileOption = None,
) -> None:
    """Show every case."""
    brokers = _brokers()
    with _profile(profile_id) as profile:
        rows = _status_rows(profile)
        numbers = counts(brokers, engine.jurisdiction_of(profile))
    if as_csv:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(STATUS_COLUMNS)
        writer.writerows(rows)
        return
    typer.echo(
        f"Broker list: {numbers['default']} companies by default for you "
        f"({numbers['default_email']} email, {numbers['default_form']} form), "
        f"{numbers['optional']} more optional."
    )
    tally: dict[str, int] = {}
    for row in rows:
        tally[str(row[1])] = tally.get(str(row[1]), 0) + 1
    typer.echo("  ".join(f"{k}: {v}" for k, v in sorted(tally.items())) or "No cases yet.")
    for row in rows:
        typer.echo(f"{row[0]:<40.40} {row[1]:<15} sent {row[2]!s:<11} due {row[3]}")


@app.command("checklist")
@guarded
def checklist_command(
    done: Annotated[str | None, typer.Option(help="Step id to mark as done.")] = None,
    profile_id: ProfileOption = None,
) -> None:
    """UK steps only you can do."""
    with _profile(profile_id) as profile:
        finished = set(profile.settings().checklist_done)
        if done:
            if done not in {s.id for s in checklist.UK_STEPS}:
                raise StealthError(f"There's no checklist step called {done}.")
            finished.add(done)
            profile.save_settings(checklist_done=sorted(finished))
    for step in checklist.UK_STEPS:
        mark = "x" if step.id in finished else " "
        typer.echo(f"[{mark}] {step.title} ({step.id})\n    {step.why}\n    {step.url}")


@app.command()
@guarded
def recover(profile_id: str, code: str) -> None:
    """Restore a profile on this computer using its recovery code."""
    store = _store()
    store.restore_key(profile_id, code)
    store.set_active(profile_id)
    typer.echo("Restored.")


@app.command()
@guarded
def demo() -> None:
    """Run a full fake cycle against a local test mail server. No real emails."""
    result = run_demo(typer.echo)
    typer.secho("\nDemo finished. Nothing left this computer.", fg=typer.colors.GREEN)
    for broker_id, state in sorted(result.statuses.items()):
        typer.echo(f"  {broker_id:<20} {state}")


@app.command()
@guarded
def brokers(country: str = "GB") -> None:
    """Show how many companies Stealth knows about."""
    numbers = counts(_brokers(), for_country(country))
    typer.echo(f"Total in the list:      {numbers['total']}")
    typer.echo(f"Written to by default:  {numbers['default']}")
    typer.echo(f"  by email:             {numbers['default_email']}")
    typer.echo(f"  by web form:          {numbers['default_form']}")
    typer.echo(f"Optional (opt-in):      {numbers['optional']}")


def run() -> None:
    app()
