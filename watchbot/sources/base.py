"""The common listing shape every source returns, and the text rules shared by the parsers."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

CONDITIONS = ("new", "excellent", "good")   # new or unworn, pre owned excellent, pre owned good
SET_TYPES = ("full_set", "watch_only")


@dataclass
class Listing:
    source: str
    source_id: str
    ref: str
    title: str
    price: float | None
    currency: str | None
    url: str
    site: str = ""
    condition: str | None = None          # one of CONDITIONS, or None when the listing does not say
    condition_text: str = ""
    year: int | None = None
    full_set: bool | None = None          # True box and papers, False stated missing, None not stated
    seller_type: str | None = None        # dealer or private
    seller_name: str | None = None
    seller_country: str | None = None     # ISO2
    item_country: str | None = None       # ISO2, where the watch is
    area: str | None = None               # Singapore area or platform
    feedback: dict = field(default_factory=dict)   # score, pct
    shipping: float | None = None         # in the listing currency, None when unknown
    buying_options: list = field(default_factory=list)
    images: list = field(default_factory=list)
    scheduled_end: str | None = None
    from_search: bool = False
    gst_on_top: bool = False              # a GST registered dealer adds GST at checkout
    service_record: bool | None = None
    text: str = ""                        # description or snippet used for flags
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def norm_ref(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def mentions_ref(text: str, ref: str, aliases=()) -> bool:
    """Does the title name this reference? Punctuation and spaces are ignored; a longer neighbouring reference
    (126610LV for 126610LN, 1243000 for 124300) does not count."""
    t = norm_ref(text)
    for r in (ref, *aliases):
        n = norm_ref(r)
        if not n:
            continue
        for m in re.finditer(re.escape(n), t):
            before = t[m.start() - 1] if m.start() else ""
            after = t[m.end()] if m.end() < len(t) else ""
            if n[-1].isdigit() and after.isdigit():
                continue
            if n[0].isdigit() and before.isdigit():
                continue
            if n[-1].isdigit() and after.isalpha() and len(n) <= 6 and _suffix_is_variant(t, m.end()):
                continue
            return True
    return False


def _suffix_is_variant(t: str, i: int) -> bool:
    """124300 followed by letters such as LN or BLRO would be a different reference; a word after it is fine
    once spaces are gone we cannot tell, so only a known variant suffix rejects."""
    return bool(re.match(r"(LN|LV|LB|BLNR|BLRO|VTNR|GRNR|CHNR|DB|LV|NG|RBR)", t[i:i + 4]))


SET_YES = re.compile(r"full\s*set|box\s*(?:and|&|\+|n)\s*papers?|\bb\s*&\s*p\b|\bbnp\b|complete\s*set|with\s+box\s+and\s+(?:card|papers?)|warranty\s*card", re.I)
SET_NO = re.compile(r"watch\s*only|no\s*box|no\s*papers?|without\s*(?:box|papers?)|head\s*only|naked", re.I)


def detect_set(text: str) -> bool | None:
    if SET_NO.search(text or ""):
        return False
    if SET_YES.search(text or ""):
        return True
    return None


YEAR = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")


def detect_year(text: str, now_year: int) -> int | None:
    for m in YEAR.finditer(text or ""):
        y = int(m.group(1))
        if 1950 <= y <= now_year:
            return y
    return None


def condition_from_text(text: str) -> str | None:
    t = (text or "").lower()
    if re.search(r"\b(brand\s*new|unworn|new\s*old\s*stock|bnib|unused)\b", t):
        return "new"
    if re.search(r"\b(mint|like\s*new|excellent|near\s*mint|pristine)\b", t):
        return "excellent"
    if re.search(r"\b(used|pre\s*owned|preowned|good\s*condition|fair|worn|scratches?)\b", t):
        return "good"
    return None


def service_from_text(text: str) -> bool | None:
    t = (text or "").lower()
    if re.search(r"(recently\s*)?serviced|service\s*(?:record|papers|history|receipt)|full\s*service", t):
        return True
    if re.search(r"no\s*service\s*(?:record|history)|never\s*serviced|due\s*for\s*service", t):
        return False
    return None
