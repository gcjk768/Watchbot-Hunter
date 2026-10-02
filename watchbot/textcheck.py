"""Checks on any text a reader sees: Claude's lines and the bot's own prose.

Rule 3 and 4: Claude may quote a number only when that exact figure is in its input. Rule 11: no dashes.
"""
from __future__ import annotations

import re

DASHES = "-‐‑‒–—―−﹘﹣－"
DASH_RE = re.compile(f"[{re.escape(DASHES)}]")
DIGITS = re.compile(r"\d+")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
PROMISES = re.compile(r"\b(guaranteed?|guarantee|easy profit|easy money|cannot lose|can't lose|can not lose|"
                      r"risk free|riskless|sure profit|no risk)\b", re.I)


def allowed_digits(numbers) -> set[str]:
    """Every digit run inside the allowed numbers: '14,800' allows 14 and 800 and 14800."""
    out: set[str] = set()
    for n in numbers:
        t = str(n)
        out.update(DIGITS.findall(t))
        out.update(DIGITS.findall(t.replace(",", "")))
    return out


def problems(text: str, numbers=(), urls=()) -> list[str]:
    found = []
    ok = allowed_digits(numbers)
    for d in DIGITS.findall(URL_RE.sub(" ", text or "")):
        if d not in ok:
            found.append(f"number {d} not in input")
    for u in URL_RE.findall(text or ""):
        if u.rstrip(".,;)") not in set(urls):
            found.append(f"url {u} not given")
    if DASH_RE.search(URL_RE.sub(" ", text or "")):
        found.append("dash")
    if m := PROMISES.search(text or ""):
        found.append(f"promise '{m.group(0)}'")
    return found


def undash(text: str) -> str:
    """Bot prose: replace dashes between words with a space or a comma, so the reader never sees one."""
    if not text:
        return text
    t = re.sub(rf"\s+[{re.escape(DASHES)}]+\s+", ", ", text)
    return DASH_RE.sub(" ", t)
