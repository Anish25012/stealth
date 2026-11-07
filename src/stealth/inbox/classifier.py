"""What a company's reply means, read the way a careful person would.

Rules, not a model: every decision can be traced to a phrase, the same email always gives the same
answer, and nothing leaves the computer. It is deliberately cautious. A reply is only "confident"
when one meaning clearly wins; mixed messages ("we've deleted your marketing data but keep your
account under a legal obligation") come out unsure, and a person reads them.

Replies quote the letter they answer, and that letter is full of words like "erase", "identity" and
"one month". Quoted text is cut off before anything is scored, so a company quoting our request back
can't make it look as if they agreed to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.message import Message
from html.parser import HTMLParser

REMOVED = "removed"
NO_DATA = "no_data"
ID_REQUESTED = "id_requested"
INFO_REQUESTED = "info_requested"
EXTENDED = "extended"
FORM_REQUIRED = "form_required"
REFUSED = "refused"
ACKNOWLEDGED = "acknowledged"  # "we've got it, we'll reply": nothing to do
AUTO_REPLY = "auto_reply"  # out of office and the like: nothing to do
UNKNOWN = "unknown"

LABELS = {
    REMOVED: "They've deleted your data",
    NO_DATA: "They hold nothing about you",
    ID_REQUESTED: "They want proof of identity",
    INFO_REQUESTED: "They want more details",
    EXTENDED: "They're taking extra time",
    FORM_REQUIRED: "They want you to use their web form",
    REFUSED: "They've refused",
    ACKNOWLEDGED: "Just confirming they got it",
    AUTO_REPLY: "An automatic reply",
    UNKNOWN: "Not sure",
}

STRONG = 2.0
OUTSTANDING_FIRST = [
    ID_REQUESTED,
    INFO_REQUESTED,
    FORM_REQUIRED,
    REFUSED,
    EXTENDED,
    REMOVED,
    NO_DATA,
]  # a score this high, unopposed, is a confident reading
NEGATION = re.compile(
    r"\b(not|unable|cannot|can't|won't|will not|haven't|have not|hasn't|has not|yet to|"
    r"no longer able|refuse|decline)\b"
)

_ = r"[\s\S]{0,40}?"  # a few words in between, lazily


@dataclass(frozen=True)
class Rule:
    kind: str
    pattern: re.Pattern[str]
    weight: float
    negatable: bool = True  # a "not" just before it cancels it


def _r(kind: str, pattern: str, weight: float = 2.0, *, negatable: bool = True) -> Rule:
    return Rule(kind, re.compile(pattern), weight, negatable)


RULES = [
    # Removed -------------------------------------------------------------------------------
    _r(
        REMOVED,
        r"\b(have|has|had)\s+(now\s+)?(been\s+)?(permanently\s+|successfully\s+)?"
        r"(deleted|erased|removed|suppressed|anonymi[sz]ed|purged)",
    ),
    _r(
        REMOVED,
        r"\b(deleted|erased|removed|purged)\s+(all\s+)?(of\s+)?(your|the)\s+(personal\s+)?"
        r"(data|information|details|records?|profile|listing|account)",
    ),
    _r(
        REMOVED,
        r"\b(request|erasure|deletion|removal)\b[\s\S]{0,30}\b(has been|is now|was)\s+"
        r"(completed|complete|processed|actioned|fulfilled)",
    ),
    _r(
        REMOVED,
        r"\b(added|placed|put)\s+(you|your\s+[\w\s]{0,25}?)\s+(to|on)\s+(our\s+)?"
        r"(suppression|do[- ]not[- ](contact|mail|sell)|opt[- ]out)",
    ),
    _r(
        REMOVED,
        r"\bno longer\s+(appear|be (listed|visible|shown|displayed|searchable)|"
        r"(hold|process|use|sell|share)\s+(any\s+)?(of\s+)?your)",
        2.0,
    ),
    _r(REMOVED, r"\b(opted|unsubscribed)\s+(you\s+)?out\b", 1.5),
    _r(REMOVED, r"\bsuccessfully\s+(deleted|removed|erased)", 2.0),
    # Held nothing ---------------------------------------------------------------------------
    _r(
        NO_DATA,
        r"\b(do|does|did)\s*(not|n't)\s+(hold|have|store|keep|process|retain)\s+(any\s+)?"
        r"(personal\s+)?(data|information|records?|details)",
        negatable=False,
    ),
    _r(
        NO_DATA,
        r"\b(no|any)\s+(personal\s+)?(data|information|records?|matches|match|results?)\b"
        r"[\s\S]{0,40}\b(found|located|held|matching|associated|relating|linked)",
        negatable=False,
    ),
    _r(
        NO_DATA,
        r"\b(could|were|was)\s*(not|n't)\s+(able to\s+)?(find|locate|identify|match)\s+"
        r"(any|you|your|a record|an account|a profile)",
        negatable=False,
    ),
    _r(
        NO_DATA,
        r"\b(not|n't)\s+(appear\s+to\s+be\s+)?(in|on|within)\s+our\s+"
        r"(database|systems?|records|files)",
        negatable=False,
    ),
    _r(NO_DATA, r"\bhold no\s+(personal\s+)?(data|information|records?)", negatable=False),
    _r(NO_DATA, r"\b(nothing|no data)\s+(to delete|to remove|held)", negatable=False),
    # Proof of identity ----------------------------------------------------------------------
    _r(
        ID_REQUESTED,
        r"\b(verify|confirm|prove|validate|authenticate)\s+(your\s+)?(identity|who you are)",
    ),
    _r(
        ID_REQUESTED,
        r"\b(proof|copy|scan|photo)\s+of\s+(your\s+)?(id\b|identity|identification|"
        r"address|passport|driv\w+ licen[cs]e|utility bill|bank statement)",
    ),
    _r(ID_REQUESTED, r"\b(photo|photographic|government[- ]issued|valid)\s+(id|identification)\b"),
    _r(ID_REQUESTED, r"\bidentity\s+(verification|check|documents?)", 2.0),
    _r(ID_REQUESTED, r"\b(passport|driving licen[cs]e|driver'?s licen[cs]e)\b", 1.0),
    # More details ---------------------------------------------------------------------------
    _r(
        INFO_REQUESTED,
        r"\b(provide|send|give|confirm)\s+(us\s+)?(with\s+)?(some\s+)?"
        r"(more|additional|further)\s+(information|details)",
    ),
    _r(
        INFO_REQUESTED,
        r"\b(which|what)\s+(email address|email|account|address|phone number|"
        r"username)\s+(you\s+)?(used|registered|did you use)",
    ),
    _r(
        INFO_REQUESTED,
        r"\b(unable|not able)\s+to\s+(locate|identify)[\s\S]{0,40}\bwithout\b",
        2.0,
        negatable=False,
    ),
    _r(INFO_REQUESTED, r"\bplease\s+(clarify|specify|tell us which)", 1.5),
    # More time ------------------------------------------------------------------------------
    _r(
        EXTENDED,
        r"\b(extend|extension|extending)\b[\s\S]{0,60}\b(two|2|further|additional)\s+months?",
    ),
    _r(EXTENDED, r"\b(additional|further|extra)\s+(two|2)\s+months?"),
    _r(
        EXTENDED,
        r"\b(complex|complexity|number of requests)\b[\s\S]{0,80}\b(extend|extension|"
        r"additional time|more time|further two)",
    ),
    _r(EXTENDED, r"\barticle\s+12\s*\(\s*3\s*\)[\s\S]{0,60}\bext", 1.5),
    # Web form -------------------------------------------------------------------------------
    _r(
        FORM_REQUIRED,
        r"\b(please|kindly|you (will )?need to|you must)\s+(use|complete|submit|fill"
        r" (in|out)|go to|visit)\s+(our|the)\s+(online\s+|dedicated\s+|secure\s+)?"
        r"(form|web ?form|portal|privacy (portal|cent(er|re))|request form|dsar form)",
        negatable=False,
    ),
    _r(
        FORM_REQUIRED,
        r"\b(can|could|are unable to|do)\s*(not|n't)?\s*(accept|process|action|"
        r"handle)\s+(these\s+|such\s+)?(requests?\s+)?(by|via|over|through)\s+e-?mail",
        negatable=False,
    ),
    _r(
        FORM_REQUIRED,
        r"\b(submit|make|raise|send)\s+(your|the|a|this)\s+request\s+(via|through|"
        r"using|on|at)\s+(our|the)\s+[\w\s]{0,20}(form|portal|page|website|link)",
        negatable=False,
    ),
    _r(
        FORM_REQUIRED,
        r"\bprivacy\s+(portal|request form|request centre|request center)\b",
        1.5,
        negatable=False,
    ),
    # Refused --------------------------------------------------------------------------------
    _r(
        REFUSED,
        r"\b(unable|not able|cannot|can't|will not|won't|not in a position)\s+to\s+"
        r"(comply|delete|erase|remove|action|fulfil|fulfill|grant|agree)",
        negatable=False,
    ),
    _r(
        REFUSED,
        r"\b(decline|refuse|reject)\w*\s+(your|this|the)\s+(request|erasure)",
        negatable=False,
    ),
    _r(
        REFUSED,
        r"\b(legitimate interests?|legal obligation|exemption|exempt)\b[\s\S]{0,80}\b"
        r"(retain|continue|keep|process|unable|not)",
        1.5,
        negatable=False,
    ),
    _r(REFUSED, r"\bmanifestly unfounded|\bmanifestly excessive", negatable=False),
    _r(
        REFUSED,
        r"\b(not|n't)\s+(subject to|covered by)\s+(the\s+)?(uk\s+)?(gdpr|data protection)",
        negatable=False,
    ),
    _r(
        REFUSED,
        r"\b(publicly available|public records?|open register|electoral roll)\b[\s\S]{0,80}"
        r"\b(continue|entitled|lawful|permitted|will (still )?(display|show|publish))",
        1.5,
        negatable=False,
    ),
    # Just acknowledging ---------------------------------------------------------------------
    _r(
        ACKNOWLEDGED,
        r"\b(we have|we've|we)\s+(successfully\s+)?received\s+your\s+(request|e-?mail|"
        r"message|enquiry|inquiry|correspondence)",
        negatable=False,
    ),
    _r(
        ACKNOWLEDGED,
        r"\b(will|aim to|endeavour to|endeavor to)\s+(respond|reply|get back|be in touch|"
        r"come back)\b[\s\S]{0,30}\b(within|in|as soon)",
        1.5,
        negatable=False,
    ),
    _r(
        ACKNOWLEDGED,
        r"\b(ticket|case|reference|request)\s*(number|no\.?|#|id)\s*[:#]?\s*\w*\d",
        1.0,
        negatable=False,
    ),
    _r(
        ACKNOWLEDGED,
        r"\b(automated|automatic)\s+(response|reply|message|acknowledg)",
        2.0,
        negatable=False,
    ),
    _r(
        ACKNOWLEDGED,
        r"\b(is being|will be)\s+(processed|reviewed|looked into|passed)",
        1.5,
        negatable=False,
    ),
    _r(
        ACKNOWLEDGED,
        r"\bthank(s| you)\s+for\s+(your|contacting|getting in touch)",
        0.5,
        negatable=False,
    ),
    # Out of office --------------------------------------------------------------------------
    _r(
        AUTO_REPLY,
        r"\bout of (the )?office\b|\bon (annual )?leave\b|\blimited access to (my )?e-?mail",
        2.5,
        negatable=False,
    ),
]

# Where quoted text starts. Everything from here down is ignored.
QUOTE_STARTS = [
    re.compile(r"^\s*on\s.{0,120}\bwrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*original message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^\s*_{8,}\s*$"),
    re.compile(r"^\s*(from|sent|von):\s.+@", re.IGNORECASE),
    re.compile(r"^\s*to the data protection officer", re.IGNORECASE),  # our own letter, pasted
    re.compile(r"^\s*-{2,}\s*forwarded message", re.IGNORECASE),
]
AUTO_SUBJECT = re.compile(
    r"^\s*(automatic reply|auto(matic)?[- ]?reply|auto:|out of (the )?office)", re.IGNORECASE
)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    kind: str
    confident: bool
    quote: str = ""  # the sentence that decided it, for showing the person
    scores: dict[str, float] = field(default_factory=dict)
    urls: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return LABELS[self.kind]

    @property
    def needs_nothing(self) -> bool:
        """Acknowledgements and out-of-office replies: no action, no "reply to read"."""
        return self.confident and self.kind in (ACKNOWLEDGED, AUTO_REPLY)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr", "blockquote", "h1", "h2", "h3"):
            self.parts.append("\n")
        if tag == "blockquote":
            self.parts.append("\n-----Original Message-----\n")  # quoted mail in HTML

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def plain_text(body: str) -> str:
    if re.search(r"<(html|body|p|div|br|table)\b", body, re.IGNORECASE):
        parser = _Text()
        parser.feed(body)
        return "".join(parser.parts)
    return body


def own_words(body: str) -> str:
    """The reply without the quoted letter underneath it."""
    kept = []
    for line in plain_text(body).splitlines():
        if any(start.search(line) for start in QUOTE_STARTS):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _normalise(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u00a0", " ")
    return re.sub(r"[ \t]+", " ", text.lower())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n{2,}|\n(?=[-*•]\s)", text) if s.strip()]


def _negated(sentence: str, start: int) -> bool:
    return bool(NEGATION.search(sentence[max(0, start - 30) : start]))


def classify(subject: str, body: str, headers: Message | None = None) -> Classification:
    words = own_words(body)
    text = _normalise(words)
    urls = tuple(dict.fromkeys(URL_RE.findall(words)))
    auto = AUTO_SUBJECT.search(subject or "") is not None
    if headers is not None:
        submitted = str(headers.get("Auto-Submitted", "")).lower()
        auto = auto or submitted.startswith("auto-") or bool(headers.get("X-Autoreply"))
    scores: dict[str, float] = {}
    best: dict[str, tuple[float, str]] = {}
    for sentence in _sentences(text):
        for rule in RULES:
            for match in rule.pattern.finditer(sentence):
                if rule.negatable and _negated(sentence, match.start()):
                    continue
                scores[rule.kind] = scores.get(rule.kind, 0.0) + rule.weight
                if rule.weight > best.get(rule.kind, (0.0, ""))[0]:
                    best[rule.kind] = (rule.weight, sentence)
                break  # one hit per rule per sentence
    kind, confident = _decide(scores, auto)
    quote = best.get(kind, (0.0, ""))[1]
    return Classification(kind, confident, _tidy(quote, words), scores, urls)


def _decide(scores: dict[str, float], auto: bool) -> tuple[str, bool]:
    strong = {k for k, v in scores.items() if v >= STRONG and k not in (ACKNOWLEDGED, AUTO_REPLY)}
    # Pairs where one meaning explains the other.
    if FORM_REQUIRED in strong:
        strong.discard(REFUSED)  # "we can't accept requests by email" is a pointer, not a refusal
    if NO_DATA in strong:
        strong.discard(REMOVED)  # "we hold nothing, and we've added you to our suppression list"
        strong.discard(REFUSED)  # "unable to delete, as we hold nothing"
        strong.discard(INFO_REQUESTED)
    if ID_REQUESTED in strong:
        strong.discard(INFO_REQUESTED)
    if EXTENDED in strong and REMOVED not in strong:
        strong.discard(ID_REQUESTED)  # rarely both; the extension is the news
    if len(strong) == 1:
        return strong.pop(), True
    if strong:
        # Mixed message ("deleted X, but need ID for Y"): the part still outstanding is the
        # best guess, and a person checks it.
        return min(strong, key=OUTSTANDING_FIRST.index), False
    if scores.get(AUTO_REPLY, 0) >= STRONG:
        return AUTO_REPLY, True
    if scores.get(ACKNOWLEDGED, 0) >= STRONG or (auto and scores.get(ACKNOWLEDGED, 0) > 0):
        weak = {k: v for k, v in scores.items() if k not in (ACKNOWLEDGED, AUTO_REPLY) and v >= 1}
        return ACKNOWLEDGED, not weak
    if auto:
        return AUTO_REPLY, True
    if scores:
        top = max(scores, key=lambda k: scores[k])
        if top not in (ACKNOWLEDGED, AUTO_REPLY):
            return top, False
    return UNKNOWN, False


def _tidy(quote: str, original: str) -> str:
    """The deciding sentence as the company wrote it, not lower-cased, and not too long."""
    if not quote:
        return ""
    flat = re.sub(r"\s+", " ", original)
    probe = re.sub(r"\s+", " ", quote)[:40]
    index = _normalise(flat).find(probe)
    text = flat[index : index + len(quote)] if index >= 0 else quote
    text = text.strip()
    return text if len(text) <= 220 else text[:217].rstrip() + "..."
