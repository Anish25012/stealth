"""Autopilot opening "click to confirm your request" links: only when every safety rule holds."""

import email.policy
import email.utils
from email.message import EmailMessage

import pytest

from stealth import autopilot, cycle
from stealth.cases.state import Status, find_case
from stealth.db import events
from stealth.devmail.servers import LocalImap, LocalSmtp, reply_to
from stealth.inbox import verify
from stealth.inbox.processor import process_inbox
from stealth_app import views
from tests.helpers import email_broker, form_broker, registry
from tests.test_lifecycle import START, World, at, go_to

DOMAINS = {"acme.example"}
GOOD = "https://acme.example/remove/verify?token=8f2c91"
CONFIRMED = "<html><body><h1>Thank you</h1><p>Your removal request is confirmed.</p></body></html>"
REAL_GET = verify.http_get  # before the test guard swaps it; only its no-network check is used
VERIFY_TEXT = "To complete your request, please click the link below to confirm:\n\n{}\n\nThanks."


def page(text=CONFIRMED, status=200, url=GOOD):
    return lambda _url, _domains: verify.Page(url, status, text)


def gmail_says(result, domain="acme.example"):
    headers = EmailMessage()
    headers["Authentication-Results"] = (
        f"mx.google.com; dkim={result} header.i=@{domain} header.s=s1; "
        f"spf={result} smtp.mailfrom=privacy@{domain}; dmarc={result} header.from={domain}"
    )
    return headers


# The rules, one at a time -----------------------------------------------------------------------


def test_a_clear_link_answering_our_letter_is_opened():
    result = verify.follow([GOOD], DOMAINS, None, page(), proven=True)
    assert result.done and result.url == GOOD
    assert result.why == "opened the confirmation link on acme.example"


def test_an_unthreaded_email_needs_the_provider_to_vouch_for_the_sender():
    assert not verify.follow([GOOD], DOMAINS, None, page()).done
    assert not verify.follow([GOOD], DOMAINS, gmail_says("pass", "evil.example"), page()).done
    assert verify.follow([GOOD], DOMAINS, gmail_says("pass"), page()).done


def test_only_the_top_authentication_header_counts():
    # Anyone sending the email can add headers below the ones their provider adds.
    headers = EmailMessage()
    headers["Authentication-Results"] = "mx.google.com; dkim=none; spf=neutral; dmarc=none"
    headers["Authentication-Results"] = "fake; dmarc=pass header.from=acme.example"
    assert not verify.vouched_for(headers, DOMAINS)


def test_a_failed_sender_check_stops_it_even_in_our_thread():
    result = verify.follow([GOOD], DOMAINS, gmail_says("fail"), page(), proven=True)
    assert not result.done and "sender checks" in result.why


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://acme.example/verify?t=1", "isn't on the company's own website"),
        ("https://acme.example.evil.example/verify?t=1", "isn't on the company's own website"),
        ("https://evil.example/acme.example/verify", "isn't on the company's own website"),
        ("https://user:pw@acme.example/verify?t=1", "isn't on the company's own website"),
        ("https://acme.example:8443/verify?t=1", "isn't on the company's own website"),
        ("https://acme.example/privacy-policy", "couldn't find the confirmation link"),
    ],
)
def test_links_that_arent_safe_are_left_for_the_person(url, why):
    result = verify.follow([url], DOMAINS, None, page(), proven=True)
    assert not result.done and why in result.why


def test_a_subdomain_of_theirs_is_fine():
    url = "https://privacy.acme.example/confirm/abc123"
    assert verify.follow([url], DOMAINS, None, page(url=url), proven=True).done


def test_two_confirmation_links_means_no_guessing():
    links = [GOOD, "https://acme.example/remove/verify?token=other"]
    result = verify.follow(links, DOMAINS, None, page(), proven=True)
    assert not result.done and "more than one link" in result.why


def test_links_elsewhere_dont_stop_a_clear_choice():
    links = ["https://acme.example/logo.png", "https://tracker.example/confirm?x=1", GOOD]
    assert verify.follow(links, DOMAINS, None, page(), proven=True).done


@pytest.mark.parametrize(
    ("served", "why"),
    [
        (page(text='<div class="g-recaptcha"></div> Thank you'), "not a robot"),
        (
            page(text="<form method=post><button>Confirm removal</button></form>"),
            "may need a click",
        ),
        (page(status=404), "error 404"),
        (page(url="https://evil.example/landing"), "another website"),
    ],
)
def test_pages_that_dont_confirm_are_left_for_the_person(served, why):
    result = verify.follow([GOOD], DOMAINS, None, served, proven=True)
    assert not result.done and why in result.why


def test_redirects_off_their_site_are_refused():
    handler = verify._SameSite(DOMAINS)
    with pytest.raises(verify.urllib.error.HTTPError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/")
    assert verify.safe_url("https://www.acme.example/done", DOMAINS)


def test_the_real_opener_refuses_an_unsafe_link_before_connecting():
    with pytest.raises(ValueError, match="not a safe link"):
        REAL_GET("http://acme.example/verify", DOMAINS)


# Through the inbox ------------------------------------------------------------------------------

BROKERS = registry(email_broker("acme"), form_broker("formco"))


@pytest.fixture
def world(profile):
    go_to(START)
    with LocalSmtp() as smtp, LocalImap("pw") as imap:
        w = World(profile, smtp, imap, BROKERS)
        profile.save_settings(autopilot=True, auto_complaints=False)
        yield w


def unthreaded(sender, subject, body, auth=None):
    message = EmailMessage(policy=email.policy.SMTP)
    message["From"] = sender
    message["To"] = "test.person@example.org"
    message["Subject"] = subject
    message["Date"] = email.utils.format_datetime(at(START))
    message["Message-ID"] = email.utils.make_msgid(domain=sender.rsplit("@", 1)[-1])
    if auth:
        message["Authentication-Results"] = auth
    message.set_content(body)
    return message.as_bytes()


def inbox(world, follow=True, served=None):
    return process_inbox(
        world.profile, world.mailbox, world.brokers, follow_links=follow, get=served or page()
    )


def test_autopilot_opens_the_link_in_a_reply_to_our_letter(world):
    world.cycle()
    address = "privacy@acme.example"
    world.imap.add(
        reply_to(world.sent_to(address).data, address, VERIFY_TEXT.format(GOOD), day=at(START))
    )
    report = inbox(world)
    assert report.verified == ["acme Ltd"] and report.replies == []
    case = find_case(world.profile.conn, "acme")
    assert case.status is Status.SENT  # still waiting for their answer, deadline unchanged
    done = autopilot.sent_automatically(world.profile)
    assert [d.what for d in done] == ["verify"] and done[0].label == "Confirmation link opened"
    history = [e.text for e in views.timeline(world.profile, case)]
    assert "Done automatically: confirmation link opened" in history


def test_a_web_form_confirmation_starts_the_legal_clock(world):
    world.cycle()
    assert find_case(world.profile.conn, "formco").status is Status.AWAITING_FORM
    url = "https://formco.example/remove/confirm?id=9"
    auth = (
        "mx.google.com; dkim=pass header.i=@formco.example; dmarc=pass header.from=formco.example"
    )
    world.imap.add(
        unthreaded("noreply@formco.example", "Confirm your removal", VERIFY_TEXT.format(url), auth)
    )
    report = inbox(world, served=page(url=url))
    assert report.verified == ["formco Ltd"]
    case = find_case(world.profile.conn, "formco")
    assert case.status is Status.SENT and case.deadline_on is not None
    trail = events.for_case(world.profile.conn, case.id)
    assert any(e["actor"] == autopilot.ACTOR and e["type"] == "status_changed" for e in trail)


def test_a_faked_sender_isnt_trusted(world):
    world.cycle()
    url = "https://formco.example/remove/confirm?id=9"
    world.imap.add(
        unthreaded("noreply@formco.example", "Confirm your removal", VERIFY_TEXT.format(url))
    )
    report = inbox(world, served=page(url=url))
    assert report.verified == [] and report.replies == ["formco Ltd"]
    case = find_case(world.profile.conn, "formco")
    assert case.status is Status.AWAITING_FORM  # still the person's to do
    skipped = [
        e for e in events.for_case(world.profile.conn, case.id) if e["type"] == "verify_skipped"
    ]
    assert "couldn't confirm the email really came from them" in skipped[0]["payload"]["reason"]
    history = [e.text for e in views.timeline(world.profile, case)]
    assert "Autopilot left their confirmation link for you" in history


def test_with_autopilot_off_the_link_is_left_for_the_person(world):
    world.cycle()
    address = "privacy@acme.example"
    world.imap.add(
        reply_to(world.sent_to(address).data, address, VERIFY_TEXT.format(GOOD), day=at(START))
    )

    def never(_url, _domains):
        raise AssertionError("opened a link with Autopilot off")

    report = inbox(world, follow=False, served=never)
    assert report.verified == [] and report.replies == ["acme Ltd"]
    assert find_case(world.profile.conn, "acme").status is Status.REPLIED


def test_the_daily_check_follows_links_only_on_autopilot(world, monkeypatch):
    seen = []
    monkeypatch.setattr(
        cycle, "process_inbox", lambda *a, **k: seen.append(k) or cycle.InboxReport()
    )
    world.cycle()
    world.profile.save_settings(autopilot=False)
    world.cycle()
    assert [k["follow_links"] for k in seen] == [True, False]
