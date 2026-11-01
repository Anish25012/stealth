"""UK steps only the person can do. Stealth tracks them but can't do them on anyone's behalf."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    why: str
    url: str


UK_STEPS = (
    Step(
        "open-register",
        "Opt out of the open electoral register",
        "Anyone can buy this list, and people-search sites such as 192.com re-list you from it.",
        "https://www.gov.uk/contact-electoral-registration-office",
    ),
    Step(
        "tps",
        "Join the Telephone Preference Service",
        "Free, and companies must not make unsolicited sales calls to registered numbers.",
        "https://www.tpsonline.org.uk/",
    ),
    Step(
        "mps",
        "Join the Mailing Preference Service",
        "Free, and reduces addressed junk mail.",
        "https://www.mpsonline.org.uk/",
    ),
    Step(
        "google-results",
        "Set up Google 'Results about you'",
        "Finds your phone, address or email in Google results and asks for them to be removed.",
        "https://myactivity.google.com/results-about-you",
    ),
)
