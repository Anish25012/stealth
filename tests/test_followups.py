"""Autopilot's follow-ups: pushing back on ID requests and chasing vague answers, once a round."""

import email.policy
import email.utils
from datetime import date, timedelta
from email.message import EmailMessage

import pytest

from stealth import autopilot, followups
from stealth.cases.state import Status, find_case
from stealth.core.jurisdictions import for_country
from stealth.devmail.servers import LocalImap, LocalSmtp
from stealth.inbox import classifier
from stealth.templates.render import render
from stealth_app import views
from tests.helpers import email_broker, registry
from tests.test_lifecycle import START, World, at, go_to

COMPANY = "privacy@acme.example"
VERIFY_IDENTITY = "Before we can action your request we need to verify your identity."
PASSPORT = "Please send us a copy of your passport or driving licence to verify your identity."
WHICH_EMAIL = "Please provide additional information: which email address did you use with us?"
OPT_OUT_ONLY = "You have been unsubscribed from our marketing emails and won't hear from us again."


@pytest.fixture
def world(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        w = World(profile, smtp, imap, registry(email_broker("acme")))
        profile.save_settings(autopilot=True, auto_complaints=False)
        w.cycle()  # the first letter goes
        yield w


def letters_to_company(world):
    return world.bodies_to(COMPANY)


def flat(text):
    return " ".join(text.split())


def done(world):
    return [
        d for d in autopilot.sent_automatically(world.profile) if d.what in ("response", "chase")
    ]


# What counts --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "documents"),
    [
        (VERIFY_IDENTITY, False),
        ("Please confirm your identity by replying from the email address on your account.", False),
        (PASSPORT, True),
        ("We need proof of address, such as a utility bill or bank statement.", True),
        ("Please attach a photo of your ID.", True),
        ("A government-issued ID is required to verify your identity.", True),
        ("To verify your identity we need a selfie holding your identity card.", True),
    ],
)
def test_requests_naming_documents_are_told_apart(text, documents):
    reading = classifier.classify("Re: Erasure", text)
    assert reading.kind == classifier.ID_REQUESTED
    assert reading.documents is documents


def test_an_opt_out_only_answer_is_vague_not_removed():
    reading = classifier.classify("Re: Erasure", OPT_OUT_ONLY)
    assert reading.kind == classifier.REMOVED and not reading.confident
    stated = classifier.classify("Re", "We have deleted all the personal data we held about you.")
    assert stated.kind == classifier.REMOVED and stated.confident


# ID requests ------------------------------------------------------------------------------------


def test_a_general_id_request_gets_polite_pushback_automatically(world):
    later = START + timedelta(days=10)
    go_to(later)
    world.reply("acme", VERIFY_IDENTITY, day=later)
    report = world.cycle()
    assert report.followups == ["acme Ltd"]
    letter = flat(letters_to_company(world)[-1])
    assert "Article 12(6)" in letter
    assert "I do not consent to sending a copy of a passport" in letter
    case = find_case(world.profile.conn, "acme")
    assert case.status is Status.SENT  # waiting again, with a fresh month from today
    assert case.deadline_on == date(2026, 2, 25)
    item = done(world)[0]
    assert item.label == "Reply to their request" and "prove who you are" in item.detail
    assert list(world.sent_to(COMPANY).message.iter_attachments()) == []  # never any documents


def test_a_request_for_actual_documents_comes_to_you(world):
    world.reply("acme", PASSPORT)
    report = world.cycle()
    assert report.followups == []
    assert len(letters_to_company(world)) == 1  # only the first letter
    case = find_case(world.profile.conn, "acme")
    assert case.status is Status.ID_REQUESTED
    row = next(r for r in views.rows(world.profile) if r.case.broker_id == "acme")
    assert row.info.group == "you"  # Needs you


def test_if_they_ask_again_after_the_pushback_it_comes_to_you(world):
    world.reply("acme", VERIFY_IDENTITY)
    world.cycle()
    go_to(START + timedelta(days=3))
    world.reply("acme", VERIFY_IDENTITY + " We cannot proceed without it.", day=START)
    report = world.cycle()
    assert report.followups == []
    assert len(letters_to_company(world)) == 2
    assert find_case(world.profile.conn, "acme").status is Status.ID_REQUESTED


def test_a_reply_only_matched_by_its_sender_isnt_answered(world):
    message = EmailMessage(policy=email.policy.SMTP)
    message["From"] = COMPANY
    message["To"] = "test.person@example.org"
    message["Subject"] = "Your request"
    message["Date"] = email.utils.format_datetime(at(START))
    message["Message-ID"] = email.utils.make_msgid(domain="acme.example")
    message.set_content(WHICH_EMAIL)
    world.imap.add(message.as_bytes())
    assert world.cycle().followups == []
    assert find_case(world.profile.conn, "acme").status is Status.REPLIED


# More details -----------------------------------------------------------------------------------


def test_a_request_for_more_details_gets_the_minimum_details(world):
    world.reply("acme", WHICH_EMAIL)
    assert world.cycle().followups == ["acme Ltd"]
    first, reply = letters_to_company(world)[0], letters_to_company(world)[-1]

    def details(text):
        prefixes = ("Name:", "Email:", "Address:", "Phone:", "Date of birth:")
        return [line.strip() for line in text.splitlines() if line.strip().startswith(prefixes)]

    assert "Email: test.person@example.org" in details(reply)
    assert details(reply) == details(first)  # the first letter's details, nothing more
    assert find_case(world.profile.conn, "acme").status is Status.SENT


# Vague answers ----------------------------------------------------------------------------------


def test_an_opt_out_only_answer_is_chased_and_the_deadline_stands(world):
    deadline = find_case(world.profile.conn, "acme").deadline_on
    world.reply("acme", OPT_OUT_ONLY)
    report = world.cycle()
    assert report.followups == ["acme Ltd"]
    letter = flat(letters_to_company(world)[-1])
    assert "erased all the personal data you hold about me under Article 17" in letter
    assert "Article 19" in letter
    assert f"please reply by {deadline.day} " in letter
    case = find_case(world.profile.conn, "acme")
    assert case.status is Status.SENT and case.deadline_on == deadline
    assert done(world)[0].label == "Chased a vague answer"
    history = [e.text for e in views.timeline(world.profile, case)]
    assert "Sent automatically: chased a vague answer" in history


def test_a_second_vague_answer_comes_to_you(world):
    world.reply("acme", OPT_OUT_ONLY)
    world.cycle()
    world.reply("acme", "You've been removed from our mailing list.")
    assert world.cycle().followups == []
    assert find_case(world.profile.conn, "acme").status is Status.REPLIED


def test_a_vague_answer_after_the_deadline_is_read_by_you_first(world):
    deadline = find_case(world.profile.conn, "acme").deadline_on
    go_to(deadline + timedelta(days=1))
    world.reply("acme", OPT_OUT_ONLY, day=deadline + timedelta(days=1))
    report = world.cycle()
    assert report.followups == [] and report.complaints == []
    assert find_case(world.profile.conn, "acme").status is Status.NEEDS_YOU


def test_unclear_replies_still_come_to_you(world):
    world.reply("acme", "Please see the attached letter.")
    assert world.cycle().followups == []
    assert find_case(world.profile.conn, "acme").status is Status.REPLIED


# Off ----------------------------------------------------------------------------------------


def test_with_autopilot_off_nothing_is_answered(world):
    world.profile.save_settings(autopilot=False)
    world.reply("acme", VERIFY_IDENTITY)
    assert world.cycle().followups == []
    assert followups.due(world.profile) == []
    assert len(letters_to_company(world)) == 1


# The chase letter in every country ------------------------------------------------------------


@pytest.mark.parametrize("country", ["GB", "FR", "CH", "AU", "CA", "US-CA"])
def test_the_chase_letter_reads_well_everywhere(country):
    letter = render(
        for_country(country),
        "chase",
        ref="STL-1A2B3C4D",
        request_date="2 November 2026",
        deadline="2 December 2026",
        on_behalf=False,
        person_name="Test Person",
        sign_off="Test Person",
    )
    text = flat(letter.body)
    assert "STL-1A2B3C4D" in letter.subject and "by 2 December 2026" in text
    assert ("{{" not in text and "Yours" in text) or "Sincerely" in text
