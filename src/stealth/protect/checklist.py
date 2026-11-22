"""The "Protect yourself" checklist: UK steps that cut what's known about you and stop scams.

Every link here was checked against the official source on CHECKED_ON. `source` says where the
address was confirmed: the service's own site, or a regulator or government page that names it.
`tests/test_protect.py` fails on any link outside OFFICIAL_HOSTS, so a typo or a lookalike site
can't slip in. Check them all again before each release that changes this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

CHECKED_ON = "2026-11-22"

# Sites the checklist may link to. Each is the service itself or the body that runs or regulates it.
OFFICIAL_HOSTS = frozenset(
    {
        "www.gov.uk",  # GOV.UK: the open register and electoral registration offices
        "www.ofcom.org.uk",  # Ofcom: names the TPS and 7726
        "www.tpsonline.org.uk",  # Telephone Preference Service
        "www.mpsonline.org.uk",  # Mailing Preference Service
        "myactivity.google.com",  # Google: Results about you
        "support.google.com",  # Google help pages
        "haveibeenpwned.com",  # Have I Been Pwned
        "www.bt.com",  # BT: Cyber Threat Protect
        "uk.norton.com",  # Norton
        "www.ncsc.gov.uk",  # National Cyber Security Centre
        "support.apple.com",  # Apple help pages
    }
)


@dataclass(frozen=True)
class Link:
    label: str
    url: str
    source: str  # where this address was confirmed

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    why: str
    steps: tuple[str, ...]
    links: tuple[Link, ...]
    # After it's done, how often to suggest checking again (None: once is enough).
    recheck_days: int | None = None
    recheck_text: str = ""


_SELF = "The service's own site"

ITEMS: tuple[Item, ...] = (
    Item(
        id="open-register",
        title="Opt out of the open register",
        why=(
            "The open register is a version of the electoral register that anyone can buy, "
            "including marketing firms and people-search sites. Opting out doesn't affect your "
            "right to vote. Your details stay on the full register, which only certain "
            "organisations can use."
        ),
        steps=(
            "Use the GOV.UK register to vote service, even if you're already registered, and "
            "choose to opt out of the open register.",
            "Or contact your council's electoral registration office and ask them to remove you "
            "from the open register.",
            "In Northern Ireland, contact the Electoral Office for Northern Ireland.",
        ),
        links=(
            Link("Register to vote (GOV.UK)", "https://www.gov.uk/register-to-vote", _SELF),
            Link(
                "About opting out of the open register (GOV.UK)",
                "https://www.gov.uk/electoral-register/opt-out-of-the-open-register",
                _SELF,
            ),
            Link(
                "Find your electoral registration office (GOV.UK)",
                "https://www.gov.uk/contact-electoral-registration-office",
                "GOV.UK's open register page",
            ),
        ),
        recheck_days=365,
        recheck_text="Check again once a year, and whenever you move or register again.",
    ),
    Item(
        id="tps",
        title="Register with the Telephone Preference Service",
        why=(
            "The TPS is the official UK list of numbers that don't want sales calls. Companies "
            "that follow the law must check it before they call. It's free and covers landlines "
            "and mobiles."
        ),
        steps=(
            "Register each of your numbers on the TPS website, or call 0345 070 0707.",
            "Allow up to 28 days for sales calls to drop off.",
            "Scammers ignore the TPS, so set up the phone scam settings below as well.",
        ),
        links=(
            Link(
                "Telephone Preference Service",
                "https://www.tpsonline.org.uk/",
                "Ofcom's page on unwanted calls",
            ),
            Link(
                "Ofcom: unwanted calls and messages",
                "https://www.ofcom.org.uk/phones-and-broadband/unwanted-calls-and-messages/"
                "live-marketing-calls",
                _SELF,
            ),
        ),
    ),
    Item(
        id="mps",
        title="Register with the Mailing Preference Service",
        why=(
            "The MPS is the free, official register for opting out of direct marketing post "
            "addressed to you. It won't stop post from companies you already deal with, such as "
            "your bank."
        ),
        steps=(
            "Register on the MPS website. You can only register other people in your home with "
            "their permission.",
            "It can take up to three months to take full effect. You don't need to renew it.",
        ),
        links=(Link("Mailing Preference Service", "https://www.mpsonline.org.uk/", _SELF),),
    ),
    Item(
        id="google-results",
        title="Ask Google to remove results showing your contact details",
        why=(
            "Google's Results about you finds search results showing your home address, phone "
            "number or email address, and lets you ask for them to be removed from Google. The "
            "page itself stays on the website, so Stealth's letters still matter."
        ),
        steps=(
            "Open Results about you while signed in to your Google Account and add the details "
            "you want it to look for.",
            "Ask for removal of each result that shows them.",
            "Turn on notifications, so Google tells you when new results appear.",
        ),
        links=(
            Link(
                "Results about you",
                "https://myactivity.google.com/results-about-you",
                "Google's help page for Results about you",
            ),
            Link(
                "How Results about you works (Google Help)",
                "https://support.google.com/websearch/answer/12719076?hl=en-GB",
                _SELF,
            ),
        ),
        recheck_days=182,
        recheck_text="Look again every six months.",
    ),
    Item(
        id="hibp",
        title="Check Have I Been Pwned for your email addresses",
        why=(
            "Have I Been Pwned lists data breaches your email address has appeared in, so you "
            "know which passwords to change. Its breach notifications are free."
        ),
        steps=(
            "Search for each email address you use.",
            "Change the password for any account in a breach, and anywhere you used the same "
            "password.",
            "Sign up for free notifications of future breaches.",
        ),
        links=(
            Link("Have I Been Pwned", "https://haveibeenpwned.com/", _SELF),
            Link("Free breach notifications", "https://haveibeenpwned.com/NotifyMe", _SELF),
        ),
        recheck_days=90,
        recheck_text="Search again every three months, unless you've turned on notifications.",
    ),
    Item(
        id="dark-web",
        title="Turn on dark web monitoring you may already have",
        why=(
            "Some broadband and security packages include dark web monitoring, which tells you if "
            "your details turn up where they shouldn't. BT broadband includes Cyber Threat "
            "Protect (the new name for BT Virus Protect), powered by Norton, with Dark Web "
            "Monitoring."
        ),
        steps=(
            "BT customers: sign in to My BT, choose Your Security, activate Cyber Threat Protect, "
            "then set up Dark Web Monitoring in its app.",
            "If you pay for Norton 360, check whether your plan includes Dark Web Monitoring and "
            "add your details to it.",
        ),
        links=(
            Link(
                "BT Cyber Threat Protect",
                "https://www.bt.com/help/security/anti-virus-software/"
                "cyber-threat-protect-and-how-do-i-get-it",
                _SELF,
            ),
            # Norton's site turns away automated checks, so this address was confirmed through
            # search results only. Check it by hand before each release.
            Link(
                "Norton Dark Web Monitoring",
                "https://uk.norton.com/feature/dark-web-monitoring",
                "Search results for Norton's UK site",
            ),
        ),
        recheck_days=365,
        recheck_text="Check once a year that it's still on and has your current details.",
    ),
    Item(
        id="passwords",
        title="Use a password manager and 2-step verification",
        why=(
            "A password manager lets you use a different strong password for every account. "
            "2-step verification (2SV) stops someone getting in with your password alone. The "
            "National Cyber Security Centre says turning on 2SV is one of the most effective "
            "ways to protect your accounts."
        ),
        steps=(
            "Choose a password manager and move your passwords into it, starting with email and "
            "banking.",
            "Turn on 2SV for your email first, since it can reset everything else.",
            "Then turn it on for banking, shopping and social media accounts.",
        ),
        links=(
            Link(
                "NCSC: password managers",
                "https://www.ncsc.gov.uk/collection/top-tips-for-staying-secure-online/"
                "password-managers",
                _SELF,
            ),
            Link(
                "NCSC: turn on 2-step verification",
                "https://www.ncsc.gov.uk/collection/top-tips-for-staying-secure-online/"
                "activate-2-step-verification-on-your-email",
                _SELF,
            ),
            Link(
                "NCSC: three random words",
                "https://www.ncsc.gov.uk/collection/top-tips-for-staying-secure-online/"
                "three-random-words",
                _SELF,
            ),
        ),
        recheck_days=365,
        recheck_text="Once a year, check every important account has 2SV on.",
    ),
    Item(
        id="phone-scams",
        title="Turn on your phone's scam call settings",
        why=(
            "Your phone can silence or screen callers you don't know, and flag likely spam, "
            "before you answer."
        ),
        steps=(
            "iPhone: go to Settings, Apps, Phone, Screen Unknown Callers. You can also go to "
            "Settings, Apps, FaceTime and turn on Silence Unknown Callers.",
            "Android with Google's Phone app: open the Phone app, tap More options, Settings, "
            "Caller ID and spam, and turn on Filter spam calls. On some phones this is part of "
            "Call Screen instead.",
            "Ask your phone or broadband provider what call blocking they offer. Some charge for "
            "it.",
        ),
        links=(
            Link(
                "Apple: manage unknown callers on iPhone",
                "https://support.apple.com/en-gb/111106",
                _SELF,
            ),
            Link(
                "Google: caller ID and spam protection",
                "https://support.google.com/phoneapp/answer/3459196?hl=en-GB",
                _SELF,
            ),
        ),
        recheck_days=365,
        recheck_text="Check once a year, and when you get a new phone.",
    ),
    Item(
        id="report-scams",
        title="Know where to report scam texts and emails",
        why=(
            "Reporting scams is free and helps get them blocked for everyone. Most UK mobile "
            "networks accept scam texts forwarded to 7726."
        ),
        steps=(
            "Save 7726 in your contacts, so it's easy to forward a scam text.",
            "Forward scam emails to report@phishing.gov.uk. Don't click any links in them.",
            "If you've lost money, contact your bank straight away.",
        ),
        links=(
            Link(
                "NCSC: report a scam text",
                "https://www.ncsc.gov.uk/collection/phishing-scams/report-scam-text-message",
                _SELF,
            ),
            Link(
                "NCSC: report a scam email",
                "https://www.ncsc.gov.uk/collection/phishing-scams/report-scam-email",
                _SELF,
            ),
        ),
    ),
    Item(
        id="social-media",
        title="Tidy up your social media",
        why=(
            "Profiles and old posts can give away your address, phone number, date of birth and "
            "who your family are: just what scammers use."
        ),
        steps=(
            "Check who can see your posts and profile on each account you use.",
            "Remove your phone number, address and date of birth from public profiles.",
            "Delete accounts you no longer use.",
        ),
        links=(
            Link(
                "NCSC: social media, how to use it safely",
                "https://www.ncsc.gov.uk/guidance/social-media-how-to-use-it-safely",
                _SELF,
            ),
        ),
        recheck_days=182,
        recheck_text="Look again every six months.",
    ),
)

BY_ID: dict[str, Item] = {item.id: item for item in ITEMS}
