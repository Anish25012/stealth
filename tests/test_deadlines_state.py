from datetime import date

import pytest

from stealth.cases import deadlines, state
from stealth.cases.state import TRANSITIONS, Status
from stealth.core.errors import InvalidTransitionError, ProfileError
from stealth.core.jurisdictions import UK
from stealth.db import events
from stealth.profiles.models import DEFAULT_SETTINGS, Settings

SETTINGS = Settings(**DEFAULT_SETTINGS)


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (date(2026, 1, 15), date(2026, 2, 15)),
        (date(2026, 1, 31), date(2026, 2, 28)),  # clamped
        (date(2028, 1, 31), date(2028, 2, 29)),  # clamped, leap year
        (date(2026, 3, 31), date(2026, 4, 30)),  # clamped
        (date(2026, 12, 31), date(2027, 1, 31)),  # year rollover
        (date(2026, 11, 30), date(2026, 12, 30)),
    ],
)
def test_one_calendar_month(start, expected):
    assert deadlines.response_deadline(start, UK) == expected


def test_extension_adds_two_further_months():
    assert deadlines.extended_deadline(date(2026, 1, 31), UK) == date(2026, 4, 30)


def test_add_months_across_several_years():
    assert deadlines.add_months(date(2026, 6, 15), 30) == date(2028, 12, 15)


def test_ico_ready_defaults_to_30_days_after_complaint():
    assert deadlines.ico_ready_on(date(2026, 1, 1), SETTINGS) == date(2026, 1, 31)


def test_resend_intervals_depend_on_category():
    start = date(2026, 1, 1)
    assert deadlines.resend_on(start, "people-search", SETTINGS) == date(2026, 3, 2)
    assert deadlines.resend_on(start, "data-aggregator", SETTINGS) == date(2026, 4, 1)


def make_case(profile, broker_id="acme", **changes):
    values = {
        "broker_id": broker_id,
        "broker_name": "Acme Data",
        "broker_domain": "acme.example",
        "category": "other",
        "method": "email",
        "contact": "p@acme.example",
        "tier": "core",
        "template": "erasure",
        "ref": f"STL-{abs(hash(broker_id)) % 10**8:08d}",
    }
    return state.create_case(profile.conn, **{**values, **changes})


def test_every_status_has_a_transition_entry():
    assert set(TRANSITIONS) == set(Status)


def test_only_terminal_statuses_lack_a_way_forward():
    assert all(TRANSITIONS[s] for s in Status)


def test_transition_updates_and_audits(profile):
    case = make_case(profile)
    moved = state.transition(
        profile.conn, case, Status.QUEUED, actor="you", reason="why", deadline_on=date(2026, 2, 1)
    )
    assert moved.status is Status.QUEUED
    assert moved.deadline_on == date(2026, 2, 1)
    last = events.for_case(profile.conn, case.id)[-1]
    assert last["actor"] == "you"
    assert last["payload"] == {"before": "draft", "after": "queued", "reason": "why"}


def test_invalid_transition_is_refused_and_leaves_case_alone(profile):
    case = make_case(profile)
    with pytest.raises(InvalidTransitionError):
        state.transition(profile.conn, case, Status.REMOVED)
    assert state.get_case(profile.conn, case.id).status is Status.DRAFT


def test_transition_rejects_unknown_columns(profile):
    case = make_case(profile)
    with pytest.raises(ValueError, match="not case columns"):
        state.transition(profile.conn, case, Status.QUEUED, status="removed")


def test_find_case_by_id_and_name(profile):
    case = make_case(profile)
    assert state.find_case(profile.conn, "acme").id == case.id
    assert state.find_case(profile.conn, "ACME da").id == case.id


def test_find_case_explains_missing_and_ambiguous(profile):
    make_case(profile, "one", broker_name="Same Name One")
    make_case(profile, "two", broker_name="Same Name Two")
    with pytest.raises(ProfileError, match="several"):
        state.find_case(profile.conn, "same name")
    with pytest.raises(ProfileError, match="No case"):
        state.find_case(profile.conn, "nothing")
