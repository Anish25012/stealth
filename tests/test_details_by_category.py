"""Each company's letters carry only the details its kind of company needs to find the person."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from stealth.cases import engine
from stealth.cases.state import Status, all_cases, find_case
from stealth.core import clock
from stealth.inbox import responses
from stealth.registry import details
from stealth.registry.details import FULL, NAME_EMAIL, WITH_PHONE
from stealth.registry.loader import load_registry
from tests.helpers import email_broker

NOON = datetime(2026, 1, 15, 12, tzinfo=UTC)
KEEP_LINE = (
    "Please do not add any of these details to your records if you do not already hold them."
)

# Real companies from the list, one of each kind, as the person's real letters would see them.
EXAMPLES = {
    "acxiom": NAME_EMAIL,  # data aggregator, outside the UK
    "cuebiq": NAME_EMAIL,  # location tracking
    "criteo": NAME_EMAIL,  # ad tech ("other")
    "lusha": WITH_PHONE,  # contact scraper
    "pipl": WITH_PHONE,  # people search
    "harte-hanks": FULL,  # marketing list
    "lexisnexis-public-records": FULL,  # public records: not narrowed
    "experian-uk": FULL,  # a UK company, whatever its category
    "captify": FULL,  # a UK marketing list
}


def broker(category, region="GLOBAL", tier="core"):
    return replace(email_broker(category=category, tier=tier), region=region)


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("data-aggregator", NAME_EMAIL),
        ("location-tracking", NAME_EMAIL),
        ("other", NAME_EMAIL),
        ("social-scraper", WITH_PHONE),
        ("people-search", WITH_PHONE),
        ("marketing-list", FULL),
        ("public-records", FULL),
        ("something-new", FULL),  # an unknown kind keeps the full set, as before
    ],
)
def test_the_rule_for_each_kind_of_company(category, expected):
    assert details.for_broker(broker(category), "core") == expected


def test_uk_companies_get_everything_and_extended_ones_name_and_email():
    for category in details.BY_CATEGORY:
        assert details.for_broker(broker(category, region="UK"), "core") == FULL
        assert details.for_broker(broker(category, region="UK"), "extended") == NAME_EMAIL
    assert details.for_broker(None, "core") == FULL


def test_the_real_list_still_files_these_companies_as_expected():
    registry = load_registry()
    for broker_id, expected in EXAMPLES.items():
        assert details.for_broker(registry[broker_id], "core") == expected, broker_id


@pytest.fixture
def drafts(profile):
    """A draft letter to each example company, from the real company list."""
    clock.set_time(NOON)
    profile.save_person(replace(profile.person(), date_of_birth="1990-01-01", dob_opt_in=True))
    registry = load_registry()
    engine.open_cases(profile, {i: registry[i] for i in EXAMPLES}, hold=True)
    assert {c.broker_id for c in all_cases(profile.conn)} == set(EXAMPLES)
    return profile


def letter_to(profile, broker_id):
    return engine.draft_letter(profile, find_case(profile.conn, broker_id)).get_content()


@pytest.mark.parametrize("broker_id", sorted(EXAMPLES))
def test_each_letter_carries_only_its_companys_details(drafts, broker_id):
    person = drafts.person()
    allowed = EXAMPLES[broker_id]
    text = letter_to(drafts, broker_id)
    for name in person.names:
        assert f"Name: {name}" in text
    for address in person.emails:
        assert f"Email: {address}" in text
    assert all((f"Phone: {p}" in text) == ("phone" in allowed) for p in person.phones)
    assert all((f"Address: {a}" in text) == ("address" in allowed) for a in person.addresses)
    assert ("Date of birth: 1990-01-01" in text) == ("date_of_birth" in allowed)
    assert KEEP_LINE in text  # every letter still asks them not to add what they don't hold


def test_the_preview_uses_the_same_rule(profile):
    clock.set_time(NOON)
    registry = load_registry()
    report = engine.preview_requests(profile, {i: registry[i] for i in EXAMPLES})
    letters = {p.name.removesuffix("-request.eml"): p.read_text("utf-8") for p in report.written}
    assert set(letters) == set(EXAMPLES)
    address, phone = profile.person().addresses[0], profile.person().phones[0]
    for broker_id, allowed in EXAMPLES.items():
        assert (address in letters[broker_id]) == ("address" in allowed), broker_id
        assert (phone in letters[broker_id]) == ("phone" in allowed), broker_id
    assert all_cases(profile.conn) == []  # a preview changes nothing


class Recorder:
    wants_bcc = False

    def __init__(self):
        self.sent = []

    def send(self, message, bcc=None):
        self.sent.append(message)


def test_later_letters_follow_the_same_rule(drafts):
    """The answer to an ID request lists details again; a complaint quotes the reference only."""
    engine.approve(drafts, ["acxiom", "harte-hanks"])
    transport = Recorder()
    registry = load_registry()
    examples = {i: registry[i] for i in EXAMPLES}  # all have cases already: nothing new opens
    engine.send_requests(drafts, examples, transport, sleep=lambda _s: None, hold_for_approval=True)
    assert len(transport.sent) == 2
    person = drafts.person()
    address, phone = person.addresses[0], person.phones[0]
    for broker_id, full in (("acxiom", False), ("harte-hanks", True)):
        case = find_case(drafts.conn, broker_id)
        assert case.status is Status.SENT
        answer = responses.response_message(drafts, case, "id").get_content()
        assert person.emails[0] in answer
        assert (address in answer) is full and (phone in answer) is full, broker_id
    clock.set_time(datetime(2026, 2, 20, 12, tzinfo=UTC))  # past both deadlines
    for broker_id in ("acxiom", "harte-hanks"):
        case = find_case(drafts.conn, broker_id)
        assert case.deadline_on < date(2026, 2, 20)
        engine.send_complaint(drafts, case, transport, grievance=engine.NO_REPLY)
        complaint = transport.sent[-1].get_content()
        assert "164A" in complaint and address not in complaint and phone not in complaint
