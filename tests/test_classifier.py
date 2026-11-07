"""Reading replies: real-world shapes of what companies send back, and what each one means."""

from email.message import Message

import pytest

from stealth.inbox import classifier as c

OUR_LETTER = """
On 2 Nov 2026, at 09:00, Sam Sample <sam@example.org> wrote:
> To the Data Protection Officer,
> I am writing to request that you erase all personal data you hold about me under Article 17.
> Please confirm within one month. If you need to verify my identity, ask only for what is needed.
"""

# (name, subject, body, expected kind, confident)
REPLIES = [
    # Removed
    (
        "deleted_plain",
        "Re: Erasure request",
        "Hello,\n\nWe have deleted your personal data from our systems.\n\n"
        "Kind regards,\nPrivacy Team",
        c.REMOVED,
        True,
    ),
    (
        "deleted_profile",
        "Your removal request",
        "Thanks for getting in touch. Your listing has been removed and will no longer appear in "
        "search results within 48 hours.",
        c.REMOVED,
        True,
    ),
    (
        "request_completed",
        "Re: Request STL-1A2B3C4D",
        "Dear Sam,\nYour erasure request has been completed. All records associated with your "
        "email address have been permanently erased.",
        c.REMOVED,
        True,
    ),
    (
        "suppression",
        "Re: Data deletion",
        "We've removed your details and added your email address to our suppression list so you "
        "won't be contacted again.",
        c.REMOVED,
        True,
    ),
    (
        "opted_out_quoted",
        "RE: Erasure request",
        "Hi Sam, we have now erased your data.\n" + OUR_LETTER,
        c.REMOVED,
        True,
    ),
    (
        "html_removed",
        "Re: Your request",
        "<html><body><p>Dear customer,</p><p>We can confirm your account and associated data "
        "<b>have been deleted</b>.</p><blockquote>To the Data Protection Officer, I need you to "
        "verify my identity</blockquote></body></html>",
        c.REMOVED,
        True,
    ),
    (
        "curly_quotes",
        "Re: Erasure",
        "We\u2019ve successfully deleted all of your information.",
        c.REMOVED,
        True,
    ),
    # Nothing held
    (
        "no_data",
        "Re: Erasure request",
        "Thank you for your request. We do not hold any personal data about you.",
        c.NO_DATA,
        True,
    ),
    (
        "no_records_found",
        "Data request",
        "Having searched our systems, no records were found matching the details you provided.",
        c.NO_DATA,
        True,
    ),
    (
        "couldnt_find",
        "Re: Removal",
        "We could not find any account linked to sam@example.org.",
        c.NO_DATA,
        True,
    ),
    (
        "not_in_database",
        "Re: DSAR",
        "You do not appear to be in our database, so there is nothing to delete.",
        c.NO_DATA,
        True,
    ),
    (
        "no_data_and_suppressed",
        "Re: Erasure",
        "We hold no personal data about you. To make sure that stays true we have added you to our "
        "do-not-contact list.",
        c.NO_DATA,
        True,
    ),
    # Proof of identity
    (
        "id_passport",
        "Re: Erasure request",
        "Before we can action this we need to verify your identity. Please send a copy of your "
        "passport or driving licence and a recent utility bill.",
        c.ID_REQUESTED,
        True,
    ),
    (
        "id_photo",
        "Identity verification required",
        "Please reply with a valid photo ID so we can process your request.",
        c.ID_REQUESTED,
        True,
    ),
    ("id_quoted_only", "Re: Erasure", "Thanks, received.\n" + OUR_LETTER, c.UNKNOWN, False),
    # More details
    (
        "which_email",
        "Re: Data deletion",
        "To find your records, could you tell us which email address you used when you registered?",
        c.INFO_REQUESTED,
        True,
    ),
    (
        "more_info",
        "Re: Erasure request",
        "Please provide us with additional information, such as your previous addresses, so we can "
        "locate your data.",
        c.INFO_REQUESTED,
        True,
    ),
    # More time
    (
        "extended",
        "Re: Erasure request",
        "Due to the complexity of your request we need to extend the deadline by a further two "
        "months, as permitted under Article 12(3).",
        c.EXTENDED,
        True,
    ),
    (
        "extra_two_months",
        "Update on your request",
        "We are writing to let you know we will need an additional two months to respond.",
        c.EXTENDED,
        True,
    ),
    # Web form
    (
        "use_form",
        "Re: Erasure request",
        "Please use our online form at https://acme.example/privacy/request to submit "
        "your request.",
        c.FORM_REQUIRED,
        True,
    ),
    (
        "no_email_requests",
        "Re: Data removal",
        "Unfortunately we cannot accept requests by email. Please submit your request via our "
        "privacy portal.",
        c.FORM_REQUIRED,
        True,
    ),
    # Refused
    (
        "refused_legal",
        "Re: Erasure request",
        "We are unable to comply with your request as we are required to retain this data under a "
        "legal obligation.",
        c.REFUSED,
        True,
    ),
    (
        "refused_public",
        "Re: Removal",
        "The information we display comes from public records and the electoral roll, and we are "
        "entitled to continue to display it.",
        c.REFUSED,
        False,
    ),
    (
        "declined",
        "Re: Erasure request",
        "We have considered your request and must decline your request for erasure.",
        c.REFUSED,
        True,
    ),
    ("unfounded", "Re: Request", "We consider this request manifestly unfounded.", c.REFUSED, True),
    # Acknowledged
    (
        "ack_ticket",
        "Request received [Ticket #48213]",
        "Thank you for contacting us. We have received your request and will respond within one "
        "month. Your ticket number is 48213.",
        c.ACKNOWLEDGED,
        True,
    ),
    (
        "ack_auto",
        "Automatic reply: Erasure request",
        "This is an automated response. Your email is being processed and a member of the team "
        "will be in touch within 30 days.",
        c.ACKNOWLEDGED,
        True,
    ),
    (
        "ack_thanks_quoted",
        "Re: Erasure request",
        "We have received your email and will get back to you as soon as possible.\n" + OUR_LETTER,
        c.ACKNOWLEDGED,
        True,
    ),
    # Out of office
    (
        "ooo",
        "Out of Office: Erasure request",
        "I am out of the office until Monday with limited access to email.",
        c.AUTO_REPLY,
        True,
    ),
    (
        "on_leave",
        "Re: Erasure",
        "I'm currently on annual leave. For urgent matters contact privacy@acme.example.",
        c.AUTO_REPLY,
        True,
    ),
    # Mixed, or unreadable: a person decides
    (
        "partial",
        "Re: Erasure request",
        "We have deleted your marketing data. However, we are unable to delete your order history "
        "as we must keep it under a legal obligation.",
        c.REFUSED,
        False,
    ),
    (
        "deleted_but_id",
        "Re: Erasure",
        "We have deleted your newsletter subscription. To delete your account we need to "
        "verify your identity first.",
        c.ID_REQUESTED,
        False,
    ),
    ("done", "Re: Erasure", "Done.", c.UNKNOWN, False),
    (
        "not_deleted_yet",
        "Re: Erasure",
        "Your data has not been deleted yet. We will write again.",
        c.UNKNOWN,
        False,
    ),
    ("empty", "Re: Erasure", "", c.UNKNOWN, False),
]


@pytest.mark.parametrize(
    ("subject", "body", "kind", "confident"),
    [r[1:] for r in REPLIES],
    ids=[r[0] for r in REPLIES],
)
def test_reading_a_reply(subject, body, kind, confident):
    result = c.classify(subject, body)
    assert (result.kind, result.confident) == (kind, confident), result.scores


def test_there_are_plenty_of_examples():
    assert len(REPLIES) >= 30
    assert {r[3] for r in REPLIES} == set(c.LABELS)  # every kind is covered


def test_the_deciding_sentence_is_quoted_as_written():
    result = c.classify("Re", "Hello.\n\nWe have DELETED your personal data. Thanks.")
    assert result.quote == "We have DELETED your personal data."


def test_links_are_picked_out():
    result = c.classify("Re", "Please use our online form at https://acme.example/erase.")
    assert result.urls == ("https://acme.example/erase.",) or result.urls == (
        "https://acme.example/erase",
    )


def test_auto_reply_headers_count():
    headers = Message()
    headers["Auto-Submitted"] = "auto-replied"
    result = c.classify("Your message", "Thanks for your email.", headers)
    assert result.needs_nothing  # an automatic message, nothing to do


def test_quoted_text_is_ignored():
    assert "verify my identity" not in c.own_words("Fine.\n" + OUR_LETTER)
    assert c.own_words("Ok\n-----Original Message-----\nFrom: x@y\nerase") == "Ok"
