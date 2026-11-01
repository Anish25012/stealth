from datetime import date

import pytest

from stealth.cases import engine
from stealth.core.errors import TemplateRenderError
from stealth.core.jurisdictions import UK
from stealth.profiles.models import MailAccount, Person
from stealth.templates.render import format_date, identity_context, render

ACCOUNT = MailAccount("smtp", "test.person@example.org")


def letter(person, account=ACCOUNT, tier="core"):
    return engine.request_message(
        person,
        account,
        UK,
        contact="dpo@acme.example",
        tier=tier,
        ref="STL-ABCD1234",
        today=date(2026, 1, 31),
    )


def body(message):
    return message.get_content()


def test_format_date_reads_like_a_uk_letter():
    assert format_date(date(2027, 3, 3)) == "3 March 2027"


@pytest.mark.parametrize(
    "phrase",
    [
        "Article 17",
        "Article 21(2)",
        "Article 21(3)",
        "Article 19",
        "give me the name of each recipient",
        "Suppression only",
        "do not add any of these details",
        "Articles 12(3) and 12A",
        "by 28 February 2026",  # the exact, clamped date
        "Article 12(6)",
        "STL-ABCD1234",
    ],
)
def test_erasure_letter_contains_every_required_clause(person, phrase):
    assert phrase in " ".join(body(letter(person)).split())


def test_letter_lists_all_the_persons_details(person):
    text = body(letter(person))
    for value in [*person.names, *person.emails, *person.addresses, *person.phones]:
        assert value in text


def test_subject_carries_the_reference(person):
    assert "STL-ABCD1234" in letter(person)["Subject"]


def test_date_of_birth_is_never_sent_without_opt_in(person):
    person.date_of_birth = "1980-02-03"
    assert "1980-02-03" not in body(letter(person))
    person.dob_opt_in = True
    assert "Date of birth: 1980-02-03" in body(letter(person))


def test_extended_letter_sends_the_minimum_and_explains_why_it_applies(person):
    person.date_of_birth = "1980-02-03"
    person.dob_opt_in = True
    text = body(letter(person, tier="extended"))
    assert "Article 3(2)" in text
    assert "1 Example Road" not in text
    assert "07700" not in text
    assert "1980-02-03" not in text
    assert person.emails[0] in text


def test_core_letter_does_not_mention_article_3(person):
    assert "Article 3(2)" not in body(letter(person))


def test_letter_for_someone_else_says_so(person):
    account = MailAccount("smtp", "helper@example.org", is_own_mailbox=False)
    text = " ".join(body(letter(person, account)).split())
    assert "on behalf of Test Person, with their authority" in text
    assert "On behalf of Test Person" in text


def test_missing_name_or_email_is_explained():
    with pytest.raises(TemplateRenderError, match="name and one email"):
        identity_context(Person(display_name="x"), ACCOUNT, extended=False)


def test_missing_template_value_never_leaves_a_blank():
    with pytest.raises(TemplateRenderError, match="missing a detail"):
        render(UK, "erasure", ref="x")


def test_unknown_template():
    with pytest.raises(TemplateRenderError, match="no letter"):
        render(UK, "nothing")


def test_message_has_standard_headers_and_no_bcc(person):
    message = letter(person)
    assert message["From"] == ACCOUNT.address
    assert message["To"] == "dpo@acme.example"
    assert message["Message-ID"]
    assert message["Date"]
    assert message["Bcc"] is None
