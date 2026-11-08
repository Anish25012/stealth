"""2.5: letters and deadlines for each place, checked against the law text they cite.

The UK is what Stealth is used for. Elsewhere the letters are lighter, and these tests pin the
legal claims they're allowed to make: a statutory deadline only where the law sets one, and no
"right to erasure" where the law doesn't give one.
"""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from stealth import cycle
from stealth.cases import deadlines, engine
from stealth.cases.state import Status, find_case
from stealth.core import clock
from stealth.core.errors import ProfileError
from stealth.core.jurisdictions import UK, choices, for_country
from stealth.devmail.servers import LocalImap, LocalSmtp
from stealth.mail.smtp_imap import ImapMailbox, SmtpTransport
from stealth.profiles.models import MailAccount
from stealth.templates.render import render
from tests.helpers import email_broker, registry

PLACES = ["GB", "FR", "CH", "US-CA", "CA", "AU"]
LETTERS = ["erasure", "complaint", "regulator", "response"]

CONTEXT = {
    "ref": "STL-ABC123",
    "deadline": "8 December 2026",
    "listing": "https://example.test/listing/1",
    "person_name": "Alex Example",
    "sign_off": "Alex Example",
    "on_behalf": False,
    "names": ["Alex Example"],
    "emails": ["alex@example.test", "old@example.test"],
    "addresses": ["1 Example Road"],
    "phones": ["01234 567890"],
    "date_of_birth": None,
    "request_date": "1 October 2026",
    "grievance": "The time limit has passed and I have not received a substantive response.",
    "contact_email": "alex@example.test",
    "broker_name": "Acme Ltd",
    "broker_domain": "acme.test",
    "complaint_date": "1 November 2026",
    "outcome": "I did not receive a substantive response by that date.",
    "complaint_outcome": "I have not received a satisfactory response to my complaint.",
    "timeline": ["1 October 2026: request sent"],
}


def letter(code, name, **extra):
    return render(for_country(code), name, **{**CONTEXT, "extended": False, "asked": "id", **extra})


# Every letter renders --------------------------------------------------------------------


@pytest.mark.parametrize("code", PLACES)
@pytest.mark.parametrize("name", LETTERS)
@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("on_behalf", [False, True])
def test_every_letter_renders_with_a_subject_and_the_reference(code, name, extended, on_behalf):
    for asked in ("id", "details"):
        out = letter(code, name, extended=extended, on_behalf=on_behalf, asked=asked)
        assert out.subject
        assert "{{" not in out.body and "{%" not in out.body
        assert "\n\n\n" not in out.body  # no gaps left by empty sections
        if name != "regulator":
            assert "STL-ABC123" in out.subject
        if on_behalf and name != "regulator":
            assert "on behalf of Alex Example" in out.body


@pytest.mark.parametrize("code", ["FR", "CH", "US-CA", "CA", "AU"])
@pytest.mark.parametrize("name", LETTERS)
def test_no_uk_law_leaks_into_other_places(code, name):
    body = letter(code, name).body
    for uk_only in ("UK GDPR", "ICO", "164A", "12A", "Data Protection Act 2018"):
        assert uk_only not in body


def test_the_uk_letters_are_unchanged_by_the_move():
    body = " ".join(letter("GB", "erasure").body.split())
    assert "Under Articles 12(3) and 12A you must respond" in body
    assert "section 164A" in " ".join(letter("GB", "complaint").body.split())
    assert "ico.org.uk/make-a-complaint" in letter("GB", "regulator").body


def test_the_extended_variant_states_why_the_law_reaches_them():
    assert "Article 3(2)" in letter("FR", "erasure", extended=True).body
    assert "Article 3(1)" in letter("CH", "erasure", extended=True).body
    assert "Article 3(2)" not in letter("FR", "erasure").body


# Only the claims the law supports --------------------------------------------------------


def flat(code, name="erasure", **extra):
    return " ".join(letter(code, name, **extra).body.split())


def test_eu_cites_one_month_and_the_extension_notice():
    body = flat("FR")
    assert "Article 12(3)" in body and "within one month" in body
    assert "tell me within that month" in body


def test_california_cites_45_days_and_both_rights():
    body = flat("US-CA")
    assert "1798.105" in body and "1798.120" in body
    assert "within 45 days" in body and "extended once by a further 45 days" in body
    # The delete right only covers what they collected from the person, so the opt-out
    # carries the request for everything else.
    assert "collected from me" in body
    assert "Whether or not you collected it from me" in body


@pytest.mark.parametrize("code", ["CH", "CA", "AU"])
def test_where_the_law_sets_no_deadline_the_letter_asks_rather_than_claims(code):
    j = for_country(code)
    assert not j.deadline_is_statutory
    body = flat(code)
    assert "you must respond" not in body and "must act on this request within" not in body
    assert "8 December 2026" in body


@pytest.mark.parametrize("code", ["CH", "CA", "AU"])
def test_no_right_to_erasure_is_claimed_where_there_is_none(code):
    for name in LETTERS:
        body = flat(code, name).lower()
        assert "right to erasure" not in body
        assert "article 17" not in body


def test_switzerland_objects_and_cites_what_the_fadp_says():
    body = flat("CH")
    assert "contrary to the express wishes" in body and "Article 30(2)(b)" in body
    assert "Article 31" in body and "Article 6(4)" in body
    assert "shall in general be given within 30 days" in body  # Art 25(7)
    assert "Article 32(2)" in flat("CH", "complaint")


def test_canada_withdraws_consent_under_schedule_1():
    body = flat("CA")
    assert "clause 4.3.8" in body and "clause 4.5.3" in body
    assert "section 11" in flat("CA", "complaint")


def test_australia_uses_app_7_and_says_reasonable_period():
    body = flat("AU")
    assert "APP 7.6" in body and "APP 7.7" in body and "APP 11.2" in body
    assert "within a reasonable period" in body and "no more than 30 days" in body


# Deadlines -------------------------------------------------------------------------------

DAY = date(2026, 1, 31)


@pytest.mark.parametrize(
    ("code", "due", "extended"),
    [
        ("GB", date(2026, 2, 28), date(2026, 4, 30)),  # one month, clamped; plus two
        ("FR", date(2026, 2, 28), date(2026, 4, 30)),
        ("US-CA", date(2026, 3, 17), date(2026, 5, 1)),  # 45 days, plus 45
        ("CH", date(2026, 3, 2), date(2026, 3, 2)),  # 30 days; no legal extension
        ("CA", date(2026, 3, 2), date(2026, 3, 2)),
        ("AU", date(2026, 3, 2), date(2026, 3, 2)),
    ],
)
def test_deadlines(code, due, extended):
    j = for_country(code)
    assert deadlines.response_deadline(DAY, j) == due
    assert deadlines.extended_deadline(DAY, j) == extended


def test_every_place_names_its_source_and_regulator():
    for code, _label in choices():
        j = for_country(code)
        assert j.deadline_source and j.regulator and j.regulator_url.startswith("https://")
        assert (j.deadline_months > 0) != (j.deadline_days > 0)


def test_choices_and_lookups():
    codes = [code for code, _ in choices()]
    assert codes[0] == "GB"
    assert {"FR", "DE", "IE", "NO", "CH", "US-CA", "CA", "AU"} <= set(codes)
    assert for_country("fr").tz == "Europe/Paris" and for_country("FR").template_dir == "eu"
    assert for_country("gb") is UK and UK.tested
    assert not any(for_country(c).tested for c in codes if c != "GB")
    with pytest.raises(ProfileError, match="doesn't support"):
        for_country("US")  # only California's law is covered


# The whole flow outside the UK -----------------------------------------------------------


def _world(profile, smtp, imap, country):
    person = profile.person()
    profile.save_person(replace(person, country=country))
    account = MailAccount(
        provider="smtp",
        address="test.person@example.org",
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_security="none",
        imap_host="127.0.0.1",
        imap_port=imap.port,
        imap_security="none",
        username="test.person@example.org",
    )
    profile.save_mail_account(account, password="pw")
    profile.save_settings(send_delay_seconds=0, include_extended=True)
    return SmtpTransport(account, "pw"), ImapMailbox(account, "pw")


def _go(day):
    clock.set_time(datetime(day.year, day.month, day.day, 10, tzinfo=UTC))


@pytest.mark.parametrize(
    ("country", "laws", "due", "words"),
    [
        ("US-CA", ("ccpa", "us-california"), date(2026, 3, 1), "1798.105"),
        ("CH", ("gdpr",), date(2026, 2, 14), "Article 30(2)(b)"),
    ],
)
def test_request_deadline_complaint_and_regulator_draft(profile, country, laws, due, words):
    start = date(2026, 1, 15)
    _go(start)
    brokers = registry(email_broker(laws=laws, tier="core"))
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        transport, mailbox = _world(profile, smtp, imap, country)

        def run():
            return cycle.run_cycle(profile, brokers, transport, mailbox, sleep=lambda _s: None)

        run()
        case = find_case(profile.conn, "acme")
        assert case.status is Status.SENT and case.deadline_on == due
        first = smtp.delivered[-1].message.get_body().get_content()
        assert words in first and "UK GDPR" not in first

        _go(date.fromordinal(due.toordinal() + 1))
        assert run().complaints == ["acme Ltd"]
        complaint = smtp.delivered[-1].message.get_body().get_content()
        assert "164A" not in complaint

        _go(date.fromordinal(due.toordinal() + 31))
        assert run().ico_ready == ["acme Ltd"]
        draft = engine.draft_ico_complaint(profile, "acme").read_text("utf-8")
        regulator = for_country(country).regulator
        assert regulator.split(" (")[0] in draft or for_country(country).regulator_abbr in draft
        assert "ICO" not in draft


def test_switzerland_writes_only_with_the_wider_list(profile):
    """No company on the list is clearly under Swiss law, so nothing is core there."""
    _go(date(2026, 1, 15))
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        transport, mailbox = _world(profile, smtp, imap, "CH")
        profile.save_settings(include_extended=False)
        brokers = registry(email_broker(laws=("gdpr",), tier="core"))
        cycle.run_cycle(profile, brokers, transport, mailbox, sleep=lambda _s: None)
        assert smtp.delivered == []


def test_pages_name_the_right_regulator():
    from stealth_app import views

    page = (
        "<p>You can now complain to the ICO, the UK's data protection regulator.</p>"
        "<button>Download ICO complaint</button><p>The ICO expects it. Use the ICO's form.</p>"
        '<h2 id="ico-h">x</h2>'
    )
    assert views.regulator_wording(page, UK) == page
    swiss = views.regulator_wording(page, for_country("CH"))
    assert "ICO" not in swiss and "UK's" not in swiss
    assert "complain to the FDPIC." in swiss and "Download FDPIC complaint" in swiss
    assert "The FDPIC expects it. Use the FDPIC's form." in swiss
    assert 'id="ico-h"' in swiss  # markup is left alone
    eu = views.regulator_wording(page, for_country("DE"))
    assert "Your data protection authority expects it." in eu
