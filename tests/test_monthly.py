"""The monthly report: replaying one month's records, the page, the PDF and the tray notice."""

from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")

from stealth.cases import engine
from stealth.cases.state import Status, create_case, find_case, transition
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth_app import monthly
from stealth_app.monthly import Month
from stealth_app.scheduler import Scheduler
from stealth_app.workspace import RealWorkspace
from tests.test_dashboard import app, client, demo, real  # noqa: F401


def at(month, day, hour=9, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def new_case(profile, broker_id, name):
    return create_case(
        profile.conn,
        broker_id=broker_id,
        broker_name=name,
        broker_domain=f"{broker_id}.test",
        category="data-aggregator",
        method="email",
        contact=f"privacy@{broker_id}.test",
        tier="core",
        template="erasure",
        ref=f"REF-{broker_id}",
    )


def letter(profile, case, kind="request", direction="out"):
    engine.save_message(
        profile,
        case,
        direction=direction,
        kind=kind,
        raw=b"Subject: test\r\n\r\nbody",
        rfc_message_id=f"<{case.broker_id}-{kind}-{clock.now().timestamp()}@test>",
        sender="someone@example.org",
        subject="test",
    )


def move(profile, broker_id, to):
    return transition(profile.conn, find_case(profile.conn, broker_id), to)


def send(profile, broker_id):
    move(profile, broker_id, Status.QUEUED)
    return move(profile, broker_id, Status.SENT)


@pytest.fixture
def history(profile):
    """Acme: written to in October, removed in November. Beta: refused in October.
    Gamma: added in November and still waiting for approval."""
    with clock.frozen_at(at(10, 5)):
        for broker_id, name in (("acme", "Acme Data"), ("beta", "Beta Lists")):
            new_case(profile, broker_id, name)
            letter(profile, send(profile, broker_id))
    with clock.frozen_at(at(10, 12)):
        letter(profile, find_case(profile.conn, "beta"), "reply", "in")
        move(profile, "beta", Status.REPLIED)
        move(profile, "beta", Status.REFUSED)
    with clock.frozen_at(at(11, 3)):
        letter(profile, find_case(profile.conn, "acme"), "reply", "in")
        move(profile, "acme", Status.REPLIED)
        move(profile, "acme", Status.REMOVED)
        new_case(profile, "gamma", "Gamma People")
    clock.set_time(at(11, 20))
    return profile


def line(report, broker_id):
    return next(item for item in report.lines if item.broker_id == broker_id)


def texts(report, broker_id):
    return [h.text for h in line(report, broker_id).happenings]


# Months ------------------------------------------------------------------------------------


def test_months_read_and_step_like_a_calendar():
    month = Month.parse("2026-12")
    assert (month.label, month.key) == ("December 2026", "2026-12")
    assert month.next() == Month(2027, 1)
    assert Month(2027, 1).previous() == month


@pytest.mark.parametrize("key", ["2026-13", "nonsense", "2026", "1999-01", "../etc"])
def test_a_made_up_month_is_refused(key):
    with pytest.raises(ProfileError):
        Month.parse(key)


def test_months_start_at_midnight_uk_time():
    # 1 October 2026 00:00 in London is still 30 September in UTC (summer time).
    assert Month(2026, 10).start("Europe/London") == datetime(2026, 9, 30, 23, 0, tzinfo=UTC)


def test_reports_exist_from_the_first_record_to_this_month(history):
    assert monthly.available(history) == [Month(2026, 11), Month(2026, 10)]
    assert monthly.last_finished(history) == Month(2026, 10)
    with pytest.raises(ProfileError):
        monthly.build(history, Month(2026, 9))
    with pytest.raises(ProfileError):
        monthly.build(history, Month(2026, 12))


# What a month says -------------------------------------------------------------------------


def test_a_past_month_shows_where_things_stood_then(history):
    october = monthly.build(history, Month(2026, 10))
    assert not october.so_far
    assert (october.letters, october.replies, october.confirmed) == (2, 1, 0)
    assert line(october, "acme").info.label == "Letter sent"  # removed only in November
    assert line(october, "beta").info.label == "Refused"
    assert "gamma" not in {item.broker_id for item in october.lines}  # didn't exist yet
    assert (october.done, october.active) == (0, 2)
    assert texts(october, "beta") == [
        "Sent a removal request",
        "They replied",
        "Refused to remove you",
    ]
    assert [n.broker_id for n in october.needs] == ["beta"]


def test_this_month_so_far(history):
    november = monthly.build(history, Month(2026, 11))
    assert november.so_far
    assert (november.letters, november.replies, november.confirmed) == (0, 1, 1)
    assert texts(november, "acme") == ["They replied", "Confirmed they removed your data"]
    assert texts(november, "gamma") == ["New letter written, waiting for your OK"]
    assert texts(november, "beta") == []
    assert [item.broker_id for item in november.lines][:2] == ["acme", "gamma"]  # news first
    assert (november.done, november.active) == (1, 3)
    assert "1 of them confirmed it this month" in november.headline


def test_the_score_is_replayed_for_the_start_and_end_of_the_month(history):
    october = monthly.build(history, Month(2026, 10))
    assert october.score_start is None  # Stealth hadn't started
    assert october.score_end.value == 100 - 5 - 1  # Beta refused, Acme still waiting
    november = monthly.build(history, Month(2026, 11))
    assert november.score_start.value == 94
    assert november.score_end.value == 100 - 5 - 1  # Acme removed; Gamma's draft is waiting
    assert november.score_change == 0


def test_a_sent_draft_isnt_reported_as_waiting(history):
    assert "New letter written, waiting for your OK" not in texts(
        monthly.build(history, Month(2026, 10)), "acme"
    )


def test_a_bounce_isnt_counted_as_a_reply(profile):
    with clock.frozen_at(at(10, 5)):
        case = new_case(profile, "dead", "Dead Address")
        letter(profile, send(profile, "dead"))
    with clock.frozen_at(at(10, 6)):
        letter(profile, case, "bounce", "in")
        move(profile, "dead", Status.BOUNCED)
    clock.set_time(at(10, 20))
    report = monthly.build(profile, Month(2026, 10))
    assert report.replies == 0
    assert texts(report, "dead") == ["Sent a removal request", "Your letter didn't reach them"]


def test_a_letter_just_after_midnight_uk_time_counts_in_the_new_month(profile):
    with clock.frozen_at(at(9, 30, 23, 30)):  # 00:30 on 1 October in London
        letter(profile, send(profile, new_case(profile, "late", "Late Ltd").broker_id))
    clock.set_time(at(10, 20))
    assert monthly.build(profile, Month(2026, 10)).letters == 1


def test_a_past_report_never_changes(history):
    before = monthly.build(history, Month(2026, 10))
    with clock.frozen_at(at(11, 21)):
        move(history, "beta", Status.ICO_READY)
    after = monthly.build(history, Month(2026, 10))
    assert (after.lines, after.score_end, after.letters) == (
        before.lines,
        before.score_end,
        before.letters,
    )


# The PDF ------------------------------------------------------------------------------------


def test_the_pdf_is_a_real_pdf_with_the_months_numbers(history):
    from stealth_app import monthly_pdf

    data = monthly_pdf.render(monthly.build(history, Month(2026, 10)))
    assert data.startswith(b"%PDF-")
    assert b"Stealth report: October 2026" in data  # the document title


def test_names_cant_inject_pdf_markup(profile):
    from stealth_app import monthly_pdf

    with clock.frozen_at(at(10, 5)):
        new_case(profile, "odd", "<b>Odd & Co</b> <font size=90>")
    clock.set_time(at(10, 20))
    assert monthly_pdf.render(monthly.build(profile, Month(2026, 10))).startswith(b"%PDF-")


# In the app ---------------------------------------------------------------------------------


def test_the_reports_page_lists_each_month_and_opens_it(client, demo):  # noqa: F811
    page = client.get("/reports")
    this = Month.containing(clock.local_date("Europe/London"))
    assert f'href="/reports/monthly/{this.key}"' in page.text
    report = client.get(f"/reports/monthly/{this.key}")
    assert report.status_code == 200
    assert this.label in report.text and "Company by company" in report.text
    assert "So far this month" in report.text
    pdf = client.get(f"/reports/monthly/{this.key}.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert f"stealth-report-{this.key}.pdf" in pdf.headers["content-disposition"]
    assert pdf.content.startswith(b"%PDF-")


def test_the_demo_has_last_month_too(client, demo):  # noqa: F811
    this = Month.containing(clock.local_date("Europe/London"))
    report = client.get(f"/reports/monthly/{this.previous().key}")
    assert report.status_code == 200
    assert "Sent a removal request" in report.text
    assert f'href="/reports/monthly/{this.key}"' in report.text  # a link on to this month


def test_a_month_without_a_report_says_so(client, demo):  # noqa: F811
    response = client.get("/reports/monthly/2001-01")
    assert response.status_code == 400
    assert "no report for January 2001" in response.text


# The tray notice ---------------------------------------------------------------------------


def test_the_tray_says_once_that_last_months_report_is_ready(store, history):
    said = []
    scheduler = Scheduler(RealWorkspace(store), notify=lambda title, text: said.append(text))
    scheduler._announce_report(history.id)
    scheduler._announce_report(history.id)
    assert said == ["Your October 2026 report is ready. Open Stealth and go to Reports to read it."]


def test_no_notice_in_the_first_month(store, profile):
    clock.set_time(at(11, 20))
    with clock.frozen_at(at(11, 5)):
        new_case(profile, "acme", "Acme Data")
    said = []
    Scheduler(RealWorkspace(store), notify=lambda title, text: said.append(text))._announce_report(
        profile.id
    )
    assert said == []
