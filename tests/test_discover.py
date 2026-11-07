"""Finding more of the person: adding a company by its website, the assisted scan, the score."""

import re

import pytest

from stealth import score
from stealth.cases import engine
from stealth.cases.state import Status, find_case
from stealth.core.errors import ProfileError, StealthError
from stealth.custom import store as custom
from stealth.custom.email_extract import find_addresses, registrable
from stealth.custom.policy_fetch import Page, look_up, normalise_domain
from stealth.devmail.sites import LocalPeopleSearch, demo_fetch
from stealth.scan.assisted import Capture

pytest.importorskip("fastapi")

from tests.test_dashboard import app, client, demo, post, real, wait_for_job  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


# Finding the address in a policy -------------------------------------------------------------


def test_the_privacy_address_comes_first_with_its_evidence():
    html = """<p>Press: press@acme.co.uk. Sales: sales@acme.co.uk.</p>
    <h2>Your rights</h2><p>To delete your personal data, email our Data Protection Officer at
    dpo@acme.co.uk.</p><p>Our parent: legal@holding.example.</p>"""
    found = find_addresses(html, "acme.co.uk")
    assert found[0].address == "dpo@acme.co.uk"
    assert "Data Protection Officer" in found[0].context
    assert found[0].same_site
    assert {c.address for c in found} >= {"press@acme.co.uk", "legal@holding.example"}
    assert not next(c for c in found if c.address == "legal@holding.example").same_site


def test_hidden_addresses_are_found():
    assert find_addresses("privacy [at] acme [dot] com", "acme.com")[0].address == (
        "privacy@acme.com"
    )
    assert find_addresses('<a href="mailto:dpo@acme.com">email us</a>', "acme.com")[0].address == (
        "dpo@acme.com"
    )


def test_things_that_only_look_like_addresses_are_not():
    html = '<img src="logo@2x.png"> write to us at acme.com'
    assert find_addresses(html, "acme.com") == []


def test_websites_are_tidied_and_nonsense_refused():
    assert normalise_domain("https://www.Acme.co.uk/about?x=1") == "acme.co.uk"
    assert normalise_domain("privacy@acme.com") == "acme.com"
    assert registrable("privacy.acme.co.uk") == "acme.co.uk"
    with pytest.raises(StealthError):
        normalise_domain("not a website")


# Reading a company's site ------------------------------------------------------------------------


def test_the_lookup_follows_the_privacy_link_and_reads_the_policy():
    found = look_up("shopwise.test", fetch=demo_fetch)
    assert found.name == "ShopWise"
    assert found.best.address == "dpo@shopwise.test"
    assert found.policy_url == "https://shopwise.test/legal/privacy-notice"


def test_the_lookup_keeps_to_the_companys_own_site_and_robots_txt():
    pages = {
        "https://acme.test/robots.txt": "User-agent: *\nDisallow: /private",
        "https://acme.test/": '<a href="https://elsewhere.test/privacy">Privacy</a>'
        '<a href="/private/privacy">Privacy (old)</a> hello@acme.test',
    }
    asked = []

    def fetch(url):
        asked.append(url)
        return Page(url, 200 if url in pages else 404, pages.get(url, ""))

    found = look_up("acme.test", fetch=fetch)
    assert not any("elsewhere.test" in url for url in asked)
    assert "https://acme.test/private/privacy" in found.blocked
    assert "https://acme.test/private/privacy" not in asked
    assert found.best.address == "hello@acme.test"


def test_an_unreachable_site_is_explained():
    def fetch(url):
        raise StealthError("Couldn't reach acme.test.")

    with pytest.raises(StealthError, match="Couldn't reach"):
        look_up("acme.test", fetch=fetch)


# Adding a company -----------------------------------------------------------------------------


def test_adding_a_company_finds_its_address_and_drafts_a_letter(client, demo):  # noqa: F811
    job, _page = wait_for_job(client, post(client, "/companies/add", site="shopwise.test"))
    assert job.state == "done", job.error
    assert job.next_url == "/companies/add/custom-shopwise-test"
    page = client.get(job.next_url).text
    assert re.search(r'value="dpo@shopwise.test"[^>]*checked', page)
    assert "Data Protection Officer" in page  # the evidence, beside the address

    response = post(
        client,
        "/companies/add/custom-shopwise-test",
        email="dpo@shopwise.test",
        name="ShopWise",
        category="marketing-list",
        listing="",
    )
    assert response.status_code == 303
    with demo.store.open() as profile:
        case = find_case(profile.conn, "custom-shopwise-test")
        assert case.status is Status.DRAFT  # waits for approval, like every other letter
        assert case.contact == "dpo@shopwise.test" and case.tier == "core"
    assert "ShopWise" in client.get("/approve").text


def test_typing_the_address_yourself(client, demo):  # noqa: F811
    wait_for_job(client, post(client, "/companies/add", site="quietlist.test"))
    post(
        client,
        "/companies/add/custom-quietlist-test",
        email="other",
        other_email="DPO@QuietList.test",
        name="QuietList",
        category="other",
        listing="https://quietlist.test/people/alex",
    )
    with demo.store.open() as profile:
        case = find_case(profile.conn, "custom-quietlist-test")
        assert case.contact == "dpo@quietlist.test"
        letter = engine.draft_letter(profile, case).get_body().get_content()
    assert "My details appear on your website here: https://quietlist.test/people/alex" in letter


def test_a_company_already_on_the_list_isnt_added_twice(client, demo):  # noqa: F811
    response = post(client, "/companies/add", site="https://www.prompt-data.test/")
    assert response.status_code == 400
    assert "already on Stealth&#39;s list" in response.text or "already on Stealth's list" in (
        response.text
    )


def test_the_demo_never_looks_up_a_real_website(client, demo):  # noqa: F811
    job, _page = wait_for_job(client, post(client, "/companies/add", site="example.com"))
    assert job.state == "failed"
    assert "made-up websites" in job.error


def test_a_custom_company_is_written_to_like_any_other(profile):
    company = custom.save_lookup(profile, look_up("shopwise.test", fetch=demo_fetch))
    custom.choose(profile, company.id, email="dpo@shopwise.test")
    engine.open_cases(profile, {}, hold=True)
    assert find_case(profile.conn, company.id).status is Status.DRAFT
    engine.approve(profile, [company.id])
    with pytest.raises(ProfileError, match="already written"):
        custom.forget(profile, company.id)  # once a letter is on its way, the record stays


# The assisted scan --------------------------------------------------------------------------


class FakeWindow:
    def __init__(self, url, text="Alex Example, 41, Leeds"):
        self.url, self.text, self.opened = url, text, []

    def open(self, url):
        self.opened.append(url)

    def capture(self):
        return Capture(self.url, "Alex Example: Find-a-Neighbour", PNG, self.text)

    def close(self):
        pass


@pytest.fixture
def window(demo):  # noqa: F811
    fake = FakeWindow(f"http://{demo.people_search.host}/search?name=Alex+Example")
    demo.search_window = lambda: fake
    return fake


def test_the_scan_page_lists_the_sites_and_the_score(client, demo, window):  # noqa: F811
    page = client.get("/scan").text
    assert "Find-a-Neighbour (demo)" in page
    assert "Exposure score" in page and "Listings aren" in page  # not checked yet, and says so
    post(client, "/scan/open", site="find-a-neighbour")
    assert window.opened == [demo.people_search.url]


def test_a_listing_after_removal_starts_a_new_letter_naming_the_page(client, demo, window):  # noqa: F811
    response = post(client, "/scan/capture")
    assert response.status_code == 303
    result_url = response.headers["location"]
    page = client.get(result_url).text
    assert "Does this show you?" in page and "Find-a-Neighbour" in page
    image = client.get(result_url + "/image")
    assert image.status_code == 200 and image.content == PNG

    post(client, result_url, outcome="listed")
    with demo.store.open() as profile:
        case = find_case(profile.conn, "find-a-neighbour")
        assert case.status is Status.DRAFT  # they'd said "held nothing": a new letter, for an OK
        assert case.custom_url == window.url
        letter = engine.draft_letter(profile, case).get_body().get_content()
        artifact = profile.conn.execute(
            "SELECT sha256 FROM artifact WHERE kind = 'scan'"
        ).fetchone()
    assert window.url in letter
    assert artifact is not None
    assert "This page shows you" in client.get(result_url).text
    assert (
        "You&#39;re listed" in client.get("/scan").text
        or "You're listed" in client.get("/scan").text
    )


def test_only_the_person_says_its_them(client, demo, window):  # noqa: F811
    result_url = post(client, "/scan/capture").headers["location"]
    post(client, result_url, outcome="not_listed")
    with demo.store.open() as profile:
        assert find_case(profile.conn, "find-a-neighbour").status is Status.NO_DATA
    assert post(client, result_url, outcome="listed").status_code == 400  # already decided


def test_a_page_on_an_unknown_site_offers_to_add_the_company(client, demo, window):  # noqa: F811
    window.url = "https://www.peoplefinder.example/alex-example"
    result_url = post(client, "/scan/capture").headers["location"]
    post(client, result_url, outcome="listed")
    page = client.get(result_url).text
    assert "Add this company" in page
    assert "site=peoplefinder.example" in page


# The score ------------------------------------------------------------------------------------


def test_the_score_follows_its_published_formula():
    statuses = [Status.REMOVED, Status.NO_DATA, Status.SENT, Status.REPLIED, Status.REFUSED]
    result = score.exposure(statuses, open_listings=2, listings_checked=True)
    assert [p.points for p in result.parts] == [20, 5, 2, 0]
    assert result.value == 73 and result.band == "medium"


def test_each_kind_is_capped():
    result = score.exposure([Status.SENT] * 100, open_listings=9, listings_checked=True)
    assert [p.points for p in result.parts] == [40, 0, 20, 0]
    assert result.value == 40


def test_a_clean_slate_scores_100():
    assert score.exposure([Status.REMOVED] * 5, open_listings=0, listings_checked=True).value == 100


# The real search window --------------------------------------------------------------------


def test_the_search_window_captures_what_the_person_searched():
    pytest.importorskip("playwright")
    from stealth.scan.assisted import NoBrowser, SearchWindow

    window = SearchWindow(headless=True)
    try:
        with LocalPeopleSearch() as site:
            try:
                window.open(site.url)
            except NoBrowser:
                pytest.skip("no Edge or Chrome on this machine")
            window.open(site.url + "search?name=Alex+Example")  # as if the person searched
            capture = window.capture()
    finally:
        window.close()
    assert capture.png.startswith(b"\x89PNG")
    assert "12 Sample Street" in capture.text
    assert capture.url.endswith("name=Alex+Example")
