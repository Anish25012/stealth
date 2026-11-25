"""Auto-send for companies the person adds: the four checks, the lists, minimal letters, the app."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from stealth import autopilot
from stealth.cases import engine
from stealth.cases.state import Status, all_cases, find_case
from stealth.core import clock
from stealth.custom import autosend, blocklist
from stealth.custom import store as custom
from stealth.custom.blocklist import BlocklistUnavailable, FeedBlocklist, FixedBlocklist
from stealth.custom.policy_fetch import Lookup, look_up
from stealth.db import events
from stealth.devmail.sites import demo_fetch
from stealth.registry.loader import Broker

pytest.importorskip("fastapi")

from tests.test_dashboard import app, client, demo, post, real, wait_for_job  # noqa: F401

REAL_DOWNLOAD = blocklist.https_download  # taken before the test guard replaces it

NOON = datetime(2026, 11, 23, 12, tzinfo=UTC)
CLEAN = FixedBlocklist()


def company(profile, *candidates, domain="acme.co.uk"):
    lookup = Lookup(domain=domain, name="Acme")
    saved = custom.save_lookup(profile, lookup)
    profile.conn.execute(
        "UPDATE custom_company SET candidates = ? WHERE id = ?",
        (json.dumps(list(candidates)), saved.id),
    )
    return custom.get(profile, saved.id)


def candidate(address, page="https://acme.co.uk/privacy", context=""):
    return {"address": address, "score": 5, "context": context, "same_site": True, "page": page}


def verdict(profile, *candidates, parents=None, lists=CLEAN, domain="acme.co.uk"):
    found = company(profile, *candidates, domain=domain)
    return autosend.judge(found, parents=parents or {}, blocklist=lists)


# The four checks -----------------------------------------------------------------------------


def test_all_four_checks_pass_for_a_privacy_address_on_the_privacy_page(profile):
    result = verdict(profile, candidate("privacy@acme.co.uk"))
    assert result.send and result.reason is None
    assert result.address == "privacy@acme.co.uk"
    assert [c.ok for c in result.checks] == [True, True, True, True]
    for local in ("dpo", "gdpr", "dataprotection", "data.protection", "privacy-uk"):
        profile.conn.execute("DELETE FROM custom_company")
        assert verdict(profile, candidate(f"{local}@acme.co.uk")).send, local


def test_an_address_on_the_home_page_isnt_sent(profile):
    result = verdict(profile, candidate("privacy@acme.co.uk", page="https://acme.co.uk/"))
    assert not result.send
    assert "isn't a privacy or contact page" in result.reason


def test_a_contact_page_counts(profile):
    assert verdict(profile, candidate("dpo@acme.co.uk", page="https://acme.co.uk/contact-us")).send


def test_an_address_on_another_website_isnt_sent(profile):
    result = verdict(profile, candidate("privacy@acme.co.uk", page="https://elsewhere.example/p"))
    assert not result.send and "own website" in result.reason


def test_an_address_on_another_domain_isnt_sent_unless_its_a_known_parent(profile):
    result = verdict(profile, candidate("privacy@holding.example"))
    assert not result.send and "not the company's website acme.co.uk" in result.reason
    profile.conn.execute("DELETE FROM custom_company")
    parent = Broker(
        id="acme",
        name="Acme",
        domain="acme.co.uk",
        region="UK",
        category="other",
        method="email",
        email="privacy@holding.example",
        form_url=None,
        notes="",
        source="test",
        last_verified="2026-11-01",
        laws=("uk-gdpr",),
        tier="core",
        confidence=1.0,
        reply_domains=(),
    )
    groups = autosend.parent_groups([parent])
    assert verdict(profile, candidate("privacy@holding.example"), parents=groups).send


def test_an_address_that_isnt_a_privacy_contact_isnt_sent(profile):
    result = verdict(profile, candidate("hello@acme.co.uk"))
    assert not result.send and "doesn't look like a privacy contact" in result.reason


def test_an_address_named_as_the_data_protection_officer_counts(profile):
    named = candidate("jane.smith@acme.co.uk", context="Our Data Protection Officer: jane.smith@")
    assert verdict(profile, named).send


def test_a_site_on_a_scam_list_isnt_sent(profile):
    lists = FixedBlocklist({"acme.co.uk"})
    result = verdict(profile, candidate("privacy@acme.co.uk"), lists=lists)
    assert not result.send and "on the PhishTank list" in result.reason


def test_if_the_lists_cant_be_checked_nothing_is_sent(profile):
    result = verdict(
        profile, candidate("privacy@acme.co.uk"), lists=FixedBlocklist(available=False)
    )
    assert not result.send and "couldn't download" in result.reason


def test_no_address_found_isnt_sent(profile):
    result = verdict(profile)
    assert not result.send and "No email address" in result.reason


def test_a_later_address_that_passes_is_used(profile):
    result = verdict(
        profile,
        candidate("hello@acme.co.uk", page="https://acme.co.uk/"),
        candidate("privacy@acme.co.uk"),
    )
    assert result.send and result.address == "privacy@acme.co.uk"


def test_the_demo_shop_passes_because_its_dpo_is_on_its_privacy_notice(profile):
    found = custom.save_lookup(profile, look_up("shopwise.test", fetch=demo_fetch))
    result = autosend.judge(found, parents={}, blocklist=CLEAN)
    assert result.send and result.address == "dpo@shopwise.test"
    assert result.page == "https://shopwise.test/legal/privacy-notice"


# The scam and phishing lists -----------------------------------------------------------------

PHISHTANK = b"""phish_id,url,phish_detail_url,submission_time,verified,verification_time,\
online,target
1,https://www.bad-bank.example/login,http://x,2026-11-22,yes,2026-11-22,yes,Other
2,http://evil.example:8080/a,http://x,2026-11-22,yes,2026-11-22,yes,Other
"""
OPENPHISH = b"https://phish.example/x\nhttp://another.example/\n"


class Downloads:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, url):
        self.calls.append(url)
        if self.fail:
            raise BlocklistUnavailable("offline")
        return PHISHTANK if "phishtank" in url else OPENPHISH


def test_lists_are_read_into_website_names():
    phishtank, openphish = blocklist.FEEDS
    assert blocklist.parse(phishtank, PHISHTANK) == {"bad-bank.example", "evil.example"}
    assert blocklist.parse(openphish, OPENPHISH) == {"phish.example", "another.example"}


def test_lists_are_checked_locally_and_downloaded_once_a_day(tmp_path):
    clock.set_time(NOON)
    download = Downloads()
    lists = FeedBlocklist(tmp_path, download=download)
    assert lists.listed({"bad-bank.example"}) == "PhishTank"
    assert lists.listed({"www.phish.example"}) == "OpenPhish"
    assert lists.listed({"acme.co.uk"}) is None
    assert len(download.calls) == 2  # each list once, whole: no website name is ever sent
    assert all("acme" not in url for url in download.calls)

    clock.set_time(NOON + timedelta(hours=23))
    FeedBlocklist(tmp_path, download=download).listed({"acme.co.uk"})
    assert len(download.calls) == 2  # still fresh

    clock.set_time(NOON + timedelta(days=2))
    download.fail = True
    assert FeedBlocklist(tmp_path, download=download).listed({"bad-bank.example"}) == "PhishTank"

    clock.set_time(NOON + timedelta(days=8))
    with pytest.raises(BlocklistUnavailable):
        FeedBlocklist(tmp_path, download=download).listed({"acme.co.uk"})


def test_no_copy_and_no_download_means_unavailable(tmp_path):
    download = Downloads()
    download.fail = True
    with pytest.raises(BlocklistUnavailable):
        FeedBlocklist(tmp_path, download=download).listed({"acme.co.uk"})


def test_tests_never_download_the_real_lists(tmp_path):
    with pytest.raises(AssertionError, match="real blocklist"):
        FeedBlocklist(tmp_path).listed({"acme.co.uk"})


def test_only_https_downloads():
    with pytest.raises(BlocklistUnavailable, match="HTTPS"):
        REAL_DOWNLOAD("http://data.phishtank.com/data/online-valid.csv")  # refused before sending


# Letters Stealth sends by itself carry name and email only ----------------------------------


def auto_company(profile):
    found = custom.save_lookup(profile, look_up("shopwise.test", fetch=demo_fetch))
    result = autosend.judge(found, parents={}, blocklist=CLEAN)
    return autosend.apply(profile, found, result)


def test_an_auto_sent_letter_has_name_and_email_only(profile):
    clock.set_time(NOON)
    person = profile.person()
    assert person.addresses and person.phones  # the profile has them...
    _company, case = auto_company(profile)
    letter = engine.draft_letter(profile, case).get_content()
    assert person.display_name in letter and person.emails[0] in letter
    for detail in (*person.addresses, *person.phones):
        assert detail not in letter  # ...but they stay out of the letter
    assert custom.minimal_details(profile, case.broker_id)


def test_a_company_chosen_by_hand_still_gets_the_usual_details(profile):
    clock.set_time(NOON)
    found = custom.save_lookup(profile, look_up("quietlist.test", fetch=demo_fetch))
    custom.choose(profile, found.id, email="privacy@quietlist.test")
    engine.open_cases(profile, {}, hold=True)
    case = find_case(profile.conn, found.id)
    assert case.status is Status.DRAFT  # waits for approval, as before
    letter = engine.draft_letter(profile, case).get_content()
    assert profile.person().addresses[0] in letter
    assert not custom.minimal_details(profile, case.broker_id)


def test_auto_send_queues_only_that_company_and_records_why(profile):
    clock.set_time(NOON)
    company_, case = auto_company(profile)
    assert case is not None and case.status is Status.QUEUED
    assert {c.broker_id for c in all_cases(profile.conn)} == {company_.id}  # nothing else
    assert company_.auto_status == "sent" and company_.email == "dpo@shopwise.test"
    trail = events.for_case(profile.conn, case.id)
    queued = next(e for e in trail if e["type"] == "status_changed")
    assert queued["actor"] == autosend.ACTOR
    assert queued["payload"]["reason"] == "sent automatically to dpo@shopwise.test"
    sent = next(e for e in trail if e["type"] == autopilot.EVENT)
    assert sent["actor"] == autopilot.ACTOR and sent["payload"]["what"] == "custom"
    assert sent["payload"]["page"] == "https://shopwise.test/legal/privacy-notice"
    assert [c["ok"] for c in sent["payload"]["checks"]] == [True, True, True, True]
    assert custom.sent_automatically(profile)[0].id == company_.id
    listed = autopilot.sent_automatically(profile)[0]
    assert listed.label == "Letter to a company you added" and "every check passed" in listed.detail


def test_a_held_company_waits_for_the_person_with_its_reason(profile):
    clock.set_time(NOON)
    found = custom.save_lookup(profile, look_up("chattybox.test", fetch=demo_fetch))
    result = autosend.judge(found, parents={}, blocklist=CLEAN)
    held, case = autosend.apply(profile, found, result)
    assert case is None and all_cases(profile.conn) == []
    assert held.auto_status == "held" and "isn't a privacy or contact page" in held.auto_reason
    assert [c.id for c in custom.held_for_you(profile)] == [held.id]
    logged = profile.conn.execute("SELECT 1 FROM event WHERE type = 'auto_send_held'").fetchone()
    assert logged is not None
    # Once the person chooses an address by hand, it's an ordinary company awaiting approval.
    custom.choose(profile, held.id, email="hello@chattybox.test")
    assert custom.held_for_you(profile) == []


# The app, under Autopilot ------------------------------------------------------------------------


def turn_on(client, start=""):  # noqa: F811
    response = post(client, "/settings/autopilot", autopilot="yes", autopilot_from=start)
    assert response.status_code == 303


def letters_to(demo, address):  # noqa: F811
    return [d for d in demo.smtp.delivered if address in d.recipients]


def test_autopilot_explains_letters_to_companies_you_add(client, demo):  # noqa: F811
    page = client.get("/settings").text
    assert "Companies you add" in page and "privacy@ or dpo@" in page
    assert "your name and email address only" in page


def test_with_autopilot_off_nothing_is_sent(client, demo):  # noqa: F811
    job, _ = wait_for_job(client, post(client, "/companies/add", site="shopwise.test"))
    assert job.next_url == "/companies/add/custom-shopwise-test"
    assert not any("automatically" in line for line in job.lines)
    with demo.store.open() as profile:
        assert all(c.broker_id != "custom-shopwise-test" for c in all_cases(profile.conn))
    assert letters_to(demo, "dpo@shopwise.test") == []


def test_before_the_start_date_nothing_is_sent(client, demo):  # noqa: F811
    turn_on(client, start="2099-01-01")
    job, _ = wait_for_job(client, post(client, "/companies/add", site="shopwise.test"))
    assert any("hasn't reached its start date" in line for line in job.lines)
    assert letters_to(demo, "dpo@shopwise.test") == []


def test_on_autopilot_a_safe_company_is_written_to_straight_away(client, demo):  # noqa: F811
    turn_on(client)
    job, _ = wait_for_job(client, post(client, "/companies/add", site="shopwise.test"))
    assert job.next_url == "/cases/custom-shopwise-test"
    assert any("All four checks passed" in line for line in job.lines)
    assert any(line.startswith("Sent.") for line in job.lines), job.lines
    sent = letters_to(demo, "dpo@shopwise.test")
    assert len(sent) == 1
    with demo.store.open() as profile:
        assert find_case(profile.conn, "custom-shopwise-test").status is Status.SENT
        person = profile.person()
    letter = sent[0].message.get_content()
    assert person.display_name in letter
    for detail in (*person.addresses, *person.phones):
        assert detail not in letter
    listing = client.get("/sent-automatically").text
    assert 'data-autopilot="custom-shopwise-test"' in listing
    assert "Letter to a company you added" in listing
    history = client.get("/cases/custom-shopwise-test").text
    assert "Sent automatically: letter to a company you added" in history


def test_on_autopilot_an_unsafe_company_waits_under_needs_you(client, demo):  # noqa: F811
    turn_on(client)
    job, _ = wait_for_job(client, post(client, "/companies/add", site="scam-shop.test"))
    assert any("Not sent automatically" in line and "PhishTank" in line for line in job.lines)
    wait_for_job(client, post(client, "/companies/add", site="chattybox.test"))
    assert letters_to(demo, "privacy@scam-shop.test") == []
    assert letters_to(demo, "hello@chattybox.test") == []
    needs = client.get("/needs").text
    assert "Companies to check" in needs
    assert 'data-held="custom-scam-shop-test"' in needs
    assert 'data-held="custom-chattybox-test"' in needs
    assert "scam and phishing sites" in needs
    assert "Not sent automatically." in client.get("/companies/add/custom-chattybox-test").text
    assert 'data-autopilot="custom-scam-shop-test"' not in client.get("/sent-automatically").text


def test_old_accounts_still_ask_before_delete_on_autopilot(client, demo):  # noqa: F811
    turn_on(client)
    wait_for_job(client, post(client, "/old-accounts/scan"))
    assert post(client, "/old-accounts/shopwise.test/delete").status_code == 400  # not confirmed
