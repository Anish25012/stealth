"""`stealth details`: seeing and changing the details letters use, with Stealth's own command."""

from stealth.cases import engine
from stealth_cli import main
from tests.test_cli import cli, ready, run  # noqa: F401


def person(home):
    with main._store().open() as profile:
        return profile.person()


def test_with_no_options_it_shows_the_details(ready):  # noqa: F811
    result = run("details")
    assert result.exit_code == 0, result.output
    assert "Full name:    Test Person" in result.output
    assert "Other names:  (none)" in result.output
    assert "1 Example Road, SW1A 1AA" in result.output


def test_a_full_update_replaces_each_list_and_the_letter_uses_it(ready):  # noqa: F811
    result = run(
        "details",
        "--name",
        "Test Q Person",
        "--email",
        "test.person@example.org",
        "--email",
        "second@example.org",
        "--address",
        "2 New Road, Leeds, LS1 1AA",
        "--phone",
        "07700 900123",
        "--yes",
    )
    assert result.exit_code == 0, result.output
    assert "Saved." in result.output
    saved = person(ready)
    assert saved.display_name == "Test Q Person"
    assert saved.names == ["Test Q Person"]  # the old name isn't kept as an "other name"
    assert saved.emails == ["test.person@example.org", "second@example.org"]
    assert saved.addresses == ["2 New Road, Leeds, LS1 1AA"]
    assert saved.phones == ["07700 900123"]
    with main._store().open() as profile:
        report = engine.preview_requests(profile, main._brokers())
    # A UK company gets every detail, so its letter shows the whole update.
    letter = next(p for p in report.written if p.name == "experian-uk-request.eml").read_text(
        encoding="utf-8"
    )
    assert "Name: Test Q Person" in letter and "Email: second@example.org" in letter
    assert "2 New Road, Leeds, LS1 1AA" in letter and "1 Example Road" not in letter


def test_lists_not_given_stay_as_they_were(ready):  # noqa: F811
    before = person(ready)
    assert run("details", "--phone", "07700 900999", "--yes").exit_code == 0
    after = person(ready)
    assert after.phones == ["07700 900999"]
    assert (after.display_name, after.emails, after.addresses) == (
        before.display_name,
        before.emails,
        before.addresses,
    )


def test_other_names_are_kept_and_can_be_cleared(ready):  # noqa: F811
    assert run("details", "--other-name", "Test Maiden", "--yes").exit_code == 0
    assert person(ready).names == ["Test Person", "Test Maiden"]
    assert run("details", "--name", "Test Newname", "--yes").exit_code == 0
    assert person(ready).names == ["Test Newname", "Test Maiden"]
    assert run("details", "--clear", "other-names", "--clear", "phones", "--yes").exit_code == 0
    assert person(ready).names == ["Test Newname"] and person(ready).phones == []


def test_it_asks_first_and_no_means_no(ready):  # noqa: F811
    result = run("details", "--name", "Someone Else", input="n\n")
    assert "After the change:" in result.output and "Nothing was changed." in result.output
    assert person(ready).display_name == "Test Person"


def test_bad_values_are_refused_and_nothing_is_saved(ready):  # noqa: F811
    result = run("details", "--email", "not-an-email", "--yes")
    assert result.exit_code == 1 and "isn't an email address" in result.output
    result = run("details", "--clear", "emails", "--yes")
    assert result.exit_code == 1 and "can't clear emails" in result.output
    result = run("details", "--name", "   ", "--yes")
    assert result.exit_code == 1 and "can't be empty" in result.output
    assert person(ready).emails == ["test.person@example.org"]
    assert person(ready).display_name == "Test Person"


def test_repeats_and_spacing_are_tidied(ready):  # noqa: F811
    assert (
        run("details", "--email", " A@Example.org ", "--email", "a@example.org", "--yes").exit_code
        == 0
    )
    assert person(ready).emails == ["A@Example.org"]


def test_nothing_to_change(ready):  # noqa: F811
    result = run("details", "--name", "Test Person", "--yes")
    assert result.exit_code == 0 and "Nothing to change." in result.output
