"""What the law offers in each place, and how long a company has to answer.

The deadline a letter states is a legal claim made in the person's name, so every one here is
checked against primary law text (the source is in `deadline_source`). Where the law sets no
deadline, `deadline_is_statutory` is False and the letters ask for a reply in that time instead of
claiming the law requires it.

The UK is what Stealth is used for. The others are lighter: their letters are checked against the
law text but haven't been used for real yet (`tested` is False), and the app says so.

What the law actually gives, as checked in November 2026:

- UK, EU/EEA: a right to erasure and to object, one month plus a possible two-month extension.
- Switzerland: no free-standing erasure right or deadline. Processing against someone's express
  wishes breaches their personality rights unless justified (FADP Arts 30(2)(b), 31), and
  deletion can be claimed in court (Art 32(2)(c)). Access requests get 30 days (Art 25(7)).
- California: deletion of data collected from the consumer, and opting out of sale or sharing,
  in 45 days plus one 45-day extension (Civ. Code 1798.105, 1798.120, 1798.130(a)(2)(A)).
  Most broker data isn't collected from the consumer, so DROP is the stronger route there.
- Canada (PIPEDA): no right to erasure. Consent can be withdrawn (Sch. 1 cl. 4.3.8), and data no
  longer needed should be destroyed (cl. 4.5.3, a recommendation). The 30-day limit in s.8(3)
  is for access requests only. Quebec's stronger law isn't covered yet.
- Australia: no right to erasure. Direct-marketing opt-out "within a reasonable period"
  (APP 7.6, 7.7), which the OAIC says is generally no more than 30 days.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from stealth.core.errors import ProfileError


@dataclass(frozen=True)
class Jurisdiction:
    code: str
    name: str
    tz: str
    regulator: str  # full name, for letters
    regulator_short: str  # in a sentence: "the ICO"
    regulator_abbr: str  # on a button: "ICO"
    regulator_url: str  # where the person makes the complaint
    law_tags: frozenset[str]  # registry `laws` values this person can rely on
    deadline_is_statutory: bool
    deadline_source: str
    template_dir: str
    deadline_months: int = 0
    deadline_days: int = 0
    extension_months: int = 0  # a further period the law lets a company take, with notice
    extension_days: int = 0
    tested: bool = False  # used for real, not just checked against the law text
    notes: str = ""  # shown to the person when they choose this place


UK = Jurisdiction(
    code="GB",
    name="United Kingdom",
    tz="Europe/London",
    regulator="Information Commissioner's Office (ICO)",
    regulator_short="the ICO",
    regulator_abbr="ICO",
    regulator_url="https://ico.org.uk/make-a-complaint/",
    law_tags=frozenset({"uk-gdpr", "gdpr"}),
    deadline_months=1,
    extension_months=2,
    deadline_is_statutory=True,
    deadline_source="UK GDPR Arts 12(3) and 12A, as amended by the Data (Use and Access) Act 2025",
    template_dir="uk",
    tested=True,
)

_EU = Jurisdiction(
    code="EU",
    name="European Union",
    tz="Europe/Brussels",
    regulator="your national data protection authority",
    regulator_short="your data protection authority",
    regulator_abbr="regulator",
    regulator_url="https://www.edpb.europa.eu/about-edpb/about-edpb/members_en",
    law_tags=frozenset({"gdpr"}),
    deadline_months=1,
    extension_months=2,
    deadline_is_statutory=True,
    deadline_source="GDPR Art 12(3): one month, extendable by two further months with notice",
    template_dir="eu",
    notes="Letters are in English. Complaints go to the data protection authority where you live.",
)

# EU and EEA countries, with the time zone a person there lives by.
_EU_COUNTRIES = {
    "AT": ("Austria", "Europe/Vienna"),
    "BE": ("Belgium", "Europe/Brussels"),
    "BG": ("Bulgaria", "Europe/Sofia"),
    "HR": ("Croatia", "Europe/Zagreb"),
    "CY": ("Cyprus", "Asia/Nicosia"),
    "CZ": ("Czechia", "Europe/Prague"),
    "DK": ("Denmark", "Europe/Copenhagen"),
    "EE": ("Estonia", "Europe/Tallinn"),
    "FI": ("Finland", "Europe/Helsinki"),
    "FR": ("France", "Europe/Paris"),
    "DE": ("Germany", "Europe/Berlin"),
    "GR": ("Greece", "Europe/Athens"),
    "HU": ("Hungary", "Europe/Budapest"),
    "IE": ("Ireland", "Europe/Dublin"),
    "IT": ("Italy", "Europe/Rome"),
    "LV": ("Latvia", "Europe/Riga"),
    "LT": ("Lithuania", "Europe/Vilnius"),
    "LU": ("Luxembourg", "Europe/Luxembourg"),
    "MT": ("Malta", "Europe/Malta"),
    "NL": ("Netherlands", "Europe/Amsterdam"),
    "PL": ("Poland", "Europe/Warsaw"),
    "PT": ("Portugal", "Europe/Lisbon"),
    "RO": ("Romania", "Europe/Bucharest"),
    "SK": ("Slovakia", "Europe/Bratislava"),
    "SI": ("Slovenia", "Europe/Ljubljana"),
    "ES": ("Spain", "Europe/Madrid"),
    "SE": ("Sweden", "Europe/Stockholm"),
    "IS": ("Iceland", "Atlantic/Reykjavik"),
    "LI": ("Liechtenstein", "Europe/Vaduz"),
    "NO": ("Norway", "Europe/Oslo"),
}

SWITZERLAND = Jurisdiction(
    code="CH",
    name="Switzerland",
    tz="Europe/Zurich",
    regulator="Federal Data Protection and Information Commissioner (FDPIC)",
    regulator_short="the FDPIC",
    regulator_abbr="FDPIC",
    regulator_url="https://www.edoeb.admin.ch/",
    law_tags=frozenset(),
    deadline_days=30,
    deadline_is_statutory=False,
    deadline_source=(
        "No statutory deadline for deletion or objection under the FADP. Stealth asks for a "
        "reply within 30 days, the period Art 25(7) FADP sets for access requests"
    ),
    template_dir="ch",
    notes=(
        "Swiss law has no deadline for deleting data, and deletion can only be enforced in "
        "court. Stealth objects, asks for deletion and asks for a reply within 30 days."
    ),
)

CALIFORNIA = Jurisdiction(
    code="US-CA",
    name="California, United States",
    tz="America/Los_Angeles",
    regulator="California Privacy Protection Agency (CalPrivacy)",
    regulator_short="CalPrivacy",
    regulator_abbr="CalPrivacy",
    regulator_url="https://cppa.ca.gov/",
    law_tags=frozenset({"ccpa", "us-california"}),
    deadline_days=45,
    extension_days=45,
    deadline_is_statutory=True,
    deadline_source=(
        "Cal. Civ. Code 1798.130(a)(2)(A) and 11 CCR 7021(b): 45 days, extendable once by "
        "45 days with notice"
    ),
    template_dir="us-ca",
    notes=(
        "The CCPA's right to delete covers data a business collected from you, which most "
        "data brokers didn't. Sign up for California's free DROP service too "
        "(consumer.drop.privacy.ca.gov): one request reaches every registered data broker."
    ),
)

CANADA = Jurisdiction(
    code="CA",
    name="Canada",
    tz="America/Toronto",
    regulator="Office of the Privacy Commissioner of Canada (OPC)",
    regulator_short="the OPC",
    regulator_abbr="OPC",
    regulator_url="https://www.priv.gc.ca/en/report-a-concern/",
    law_tags=frozenset(),
    deadline_days=30,
    deadline_is_statutory=False,
    deadline_source=(
        "No statutory deadline for withdrawing consent under PIPEDA. Stealth asks for a reply "
        "within 30 days, the period s.8(3) PIPEDA sets for access requests"
    ),
    template_dir="ca",
    notes=(
        "Canada's federal law has no right to erasure: Stealth withdraws your consent and asks "
        "companies to destroy what they no longer need. Quebec's stronger rights aren't "
        "covered yet."
    ),
)

AUSTRALIA = Jurisdiction(
    code="AU",
    name="Australia",
    tz="Australia/Sydney",
    regulator="Office of the Australian Information Commissioner (OAIC)",
    regulator_short="the OAIC",
    regulator_abbr="OAIC",
    regulator_url=(
        "https://www.oaic.gov.au/privacy/privacy-complaints/lodge-a-privacy-complaint-with-us"
    ),
    law_tags=frozenset(),
    deadline_days=30,
    deadline_is_statutory=False,
    deadline_source=(
        "APP 7.7: 'within a reasonable period'. The OAIC's APP guidelines (para 7.37) say "
        "that is generally no more than 30 days"
    ),
    template_dir="au",
    notes=(
        "Australia has no right to erasure yet: the 2026 reform bill is still a draft. Stealth "
        "opts you out of direct marketing and asks companies to destroy what they no longer need."
    ),
)

_ALL = {j.code: j for j in (UK, SWITZERLAND, CALIFORNIA, CANADA, AUSTRALIA)}


def _eu_country(code: str) -> Jurisdiction:
    name, tz = _EU_COUNTRIES[code]
    return replace(_EU, code=code, name=name, tz=tz)


def for_country(country: str) -> Jurisdiction:
    code = country.upper()
    if code in _ALL:
        return _ALL[code]
    if code in _EU_COUNTRIES:
        return _eu_country(code)
    raise ProfileError(
        f"Stealth doesn't support {country} yet.",
        hint="Supported: the UK, EU and EEA countries, Switzerland, California, Canada and "
        "Australia.",
    )


def choices() -> list[tuple[str, str]]:
    """Every place a person can choose, UK first, for the setup form."""
    others = [(j.code, j.name) for j in _ALL.values() if j.code != UK.code]
    eu = [(code, f"{name} (EU/EEA)") for code, (name, _tz) in _EU_COUNTRIES.items()]
    return [(UK.code, UK.name), *sorted(others + eu, key=lambda c: c[1])]
