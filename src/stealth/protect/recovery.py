"""The recovery playbook: what to do, in order, when something has gone wrong.

Each situation is a list of actions. An action says what to do and why, with the official link or
phone number. Every link and number was confirmed on CHECKED_ON against the source named next to it
(the service's own site, or a GOV.UK, NCSC or regulator page that names it), and
`tests/test_recovery.py` fails on any link outside OFFICIAL_HOSTS. Check them all again before each
release that changes this file.

Report Fraud replaced Action Fraud on 4 December 2025, keeping the phone number 0300 123 2040. It
covers England, Wales and Northern Ireland; in Scotland, fraud goes to Police Scotland on 101.
"""

from __future__ import annotations

from dataclasses import dataclass

from stealth.protect import cifas, credit
from stealth.protect.checklist import Link

CHECKED_ON = "2026-11-22"

OFFICIAL_HOSTS = frozenset(
    {
        "www.reportfraud.police.uk",  # Report Fraud (City of London Police)
        "www.ncsc.gov.uk",  # National Cyber Security Centre
        "www.gov.uk",  # GOV.UK: reporting scam emails and texts
        "ico.org.uk",  # Information Commissioner's Office
        "www.stopscamsuk.org.uk",  # Stop Scams UK: the 159 line
        *cifas.OFFICIAL_HOSTS,
        *credit.OFFICIAL_HOSTS,
    }
)


@dataclass(frozen=True)
class Phone:
    label: str
    number: str
    source: str  # where this number was confirmed


@dataclass(frozen=True)
class Action:
    id: str
    title: str
    what: str  # what to do
    why: str
    links: tuple[Link, ...] = ()
    phones: tuple[Phone, ...] = ()
    in_app: tuple[tuple[str, str], ...] = ()  # (label, Stealth page) for things Stealth helps with
    urgent: bool = False  # do it now: kept at the top on screen until it's done


@dataclass(frozen=True)
class Situation:
    id: str
    title: str
    summary: str
    actions: tuple[Action, ...]


# The official sources -------------------------------------------------------------------------

REPORT_FRAUD = Link(
    "Report Fraud",
    "https://www.reportfraud.police.uk/",
    "Named on GOV.UK's phishing page and the NCSC's hacked-account guide",
)
REPORT_FRAUD_PHONE = Phone(
    "Report Fraud (England, Wales and Northern Ireland)",
    "0300 123 2040",
    "GOV.UK's phishing page and the NCSC's scam email page",
)
POLICE_SCOTLAND = Phone(
    "Police Scotland (if you live in Scotland)", "101", "The NCSC's scam email page"
)
NCSC_HACKED = Link(
    "Recovering a hacked account (NCSC)",
    "https://www.ncsc.gov.uk/guidance/recovering-a-hacked-account",
    "The NCSC's own site",
)
GOV_PHISHING = Link(
    "Report scam emails and texts (GOV.UK)",
    "https://www.gov.uk/report-suspicious-emails-websites-phishing",
    "GOV.UK",
)
ICO_COMPLAIN = Link(
    "Make a complaint (ICO)", "https://ico.org.uk/make-a-complaint/", "The ICO's own site"
)
ICO_PHONE = Phone("ICO helpline", "0303 123 1113", "The ICO's contact pages")
STOP_SCAMS = Link(
    "About 159 (Stop Scams UK)", "https://www.stopscamsuk.org.uk/159", "Stop Scams UK's own site"
)
BANK_PHONE = Phone(
    "159: puts you through to most UK banks' fraud teams", "159", "Stop Scams UK's own site"
)
CREDIT_LINKS = tuple(agency.report for agency in credit.AGENCIES)
SCAM_EMAIL = "report@phishing.gov.uk"


# The actions ----------------------------------------------------------------------------------

BANK = Action(
    "bank",
    "Call your bank's fraud line now",
    "Use the number on the back of your card or on your bank's own website, never one from a "
    "message or a caller. Ask them to stop the card, check recent payments and tell you what "
    "else to do. Write down the reference they give you.",
    "Banks can often stop or get back payments if they hear quickly, and the reference helps if "
    "you need to claim later.",
    links=(STOP_SCAMS,),
    phones=(BANK_PHONE,),
    urgent=True,
)
STATEMENTS = Action(
    "statements",
    "Go through your statements",
    "Check every account and card for payments you don't recognise, going back a few months, and "
    "tell your bank about each one.",
    "Fraudsters often try a small payment first. Anything you report quickly is easier to get "
    "back.",
)
REPORT = Action(
    "report-fraud",
    "Report it to the police (Report Fraud)",
    "Report online or by phone if you've lost money or someone used your identity. In Scotland, "
    "call Police Scotland on 101 instead. Keep the crime reference number.",
    "It's the UK's national service for fraud and cyber crime, run by the City of London Police. "
    "Banks and lenders may ask for the reference.",
    links=(REPORT_FRAUD,),
    phones=(REPORT_FRAUD_PHONE, POLICE_SCOTLAND),
)
CREDIT = Action(
    "credit-reports",
    "Check all three credit reports",
    "Get your free statutory report from Experian, Equifax and TransUnion. Look for accounts, "
    "searches or addresses you don't recognise, and ask the agency to correct anything wrong.",
    "Each agency holds different records, so something may show up on only one of them.",
    links=CREDIT_LINKS,
    in_app=(("Credit report reminders", "/protect#credit-experian"),),
)
CIFAS = Action(
    "cifas",
    "Think about Cifas Protective Registration",
    "Apply on Cifas's website. It costs £30 for two years and you can't apply by phone.",
    "Lenders that check it take extra care before opening anything in your name, which makes it "
    "harder to use your identity again.",
    links=(cifas.APPLY,),
    in_app=(("Track it in Stealth", "/protect#cifas"),),
)
LENDER = Action(
    "lender",
    "Tell the lender it wasn't you",
    "Call the fraud team of each lender or company where something was opened, using the number on "
    "its own website. Ask them to close it, record it as fraud and confirm in writing.",
    "Until they do, the debt can be chased and can harm your credit file.",
    urgent=True,
)
RECOVER = Action(
    "recover-account",
    "Get back into the account",
    "Use the provider's own account recovery page (search for it yourself, don't follow a link "
    "you were sent). If you can't get back in, set up a new account and tell people.",
    "The provider can confirm it's you and lock the hacker out.",
    links=(NCSC_HACKED,),
    urgent=True,
)
PASSWORDS = Action(
    "passwords",
    "Change the password, and anywhere you used it",
    "Give the account a new, strong password that you don't use anywhere else. Then change it on "
    "every other account that had the same one, starting with your email.",
    "Criminals try leaked passwords on other sites. Your email matters most because it can reset "
    "everything else.",
    links=(NCSC_HACKED,),
)
SIGN_OUT = Action(
    "sign-out",
    "Sign out everywhere and check the settings",
    "Sign out all other devices and apps. Check for email forwarding rules, and recovery phone "
    "numbers and email addresses you didn't add, and remove them.",
    "Hackers often leave a way back in, such as a rule that forwards your mail to them.",
    links=(NCSC_HACKED,),
)
TWO_STEP = Action(
    "two-step",
    "Turn on 2-step verification",
    "Turn it on for the account, and for your email and bank if it isn't already.",
    "Even with your password, someone then needs your phone or a code to get in.",
    links=(NCSC_HACKED,),
)
CONTACTS = Action(
    "contacts",
    "Warn your contacts",
    "Tell friends and family the account was hacked, and to ignore any messages asking for money "
    "or codes that seemed to come from you.",
    "Hackers use a hacked account to scam the people who trust you.",
)
SCAM_MESSAGES = Action(
    "report-scam",
    "Report the scam messages",
    f"Forward scam emails to {SCAM_EMAIL} and scam texts to 7726 (it's free). Then delete them.",
    "It helps take down the scammers' websites and numbers.",
    links=(GOV_PHISHING,),
)
FOLLOW_UP = Action(
    "follow-up-scams",
    "Watch out for follow-up scams",
    "Be wary of anyone who gets in touch offering to recover your money, or claiming to be your "
    "bank or the police. Hang up and call back on a number you know is real.",
    "People who've been scammed are often targeted again, sometimes by the same criminals.",
    links=(GOV_PHISHING,),
)
BREACH_COMPANY = Action(
    "breach-company",
    "Ask the company what happened",
    "Ask the company that had the breach what it lost and what it's doing. If you're not happy "
    "with its answer, you can complain to the ICO.",
    "Companies must keep your data safe, and the ICO can act if they didn't.",
    links=(ICO_COMPLAIN,),
    phones=(ICO_PHONE,),
)
BREACH_WATCH = Action(
    "breach-watch",
    "Turn on breach monitoring",
    "Let Stealth tell you if your email addresses turn up in another breach.",
    "The sooner you know, the sooner you can change the password.",
    in_app=(("Data breaches in Stealth", "/protect#breaches"),),
)

SITUATIONS: tuple[Situation, ...] = (
    Situation(
        "account-hacked",
        "My email or social media account was hacked",
        "Someone got into an account and may be using it or locking you out.",
        (RECOVER, PASSWORDS, SIGN_OUT, TWO_STEP, CONTACTS, STATEMENTS, REPORT),
    ),
    Situation(
        "card-used",
        "My bank card or account was used",
        "Payments you didn't make, or money missing from an account.",
        (BANK, STATEMENTS, REPORT, PASSWORDS, CREDIT, CIFAS),
    ),
    Situation(
        "credit-opened",
        "Someone opened credit in my name",
        "A loan, card, phone contract or account you didn't apply for.",
        (LENDER, BANK, CREDIT, REPORT, CIFAS, FOLLOW_UP),
    ),
    Situation(
        "scammer",
        "I gave details to a scammer",
        "You shared card, bank, password or personal details with someone who wasn't who they "
        "said.",
        (BANK, PASSWORDS, REPORT, SCAM_MESSAGES, CREDIT, CIFAS, FOLLOW_UP),
    ),
    Situation(
        "breach",
        "My data was in a breach",
        "A company told you, or you found out, that your details were exposed.",
        (PASSWORDS, TWO_STEP, FOLLOW_UP, BREACH_COMPANY, CREDIT, BREACH_WATCH),
    ),
)
BY_ID = {s.id: s for s in SITUATIONS}


def all_links() -> list[Link]:
    return [link for s in SITUATIONS for a in s.actions for link in a.links]
