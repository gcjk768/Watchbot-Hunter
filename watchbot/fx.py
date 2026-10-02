"""Exchange rates into SGD, one API call a day (ECB reference rates through frankfurter). Stored per date in fx.

A price in a currency with no stored rate within 7 days converts to None, never to a guess.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

log = logging.getLogger(__name__)


def refresh(db, lim, s, transport: httpx.BaseTransport | None = None) -> int:
    today = lim.today()
    if db.execute("SELECT 1 FROM fx WHERE date=? LIMIT 1", (today,)).fetchone():
        return 0
    cur = ",".join(s.sources.fx.currencies)
    lim.acquire("fx", "frankfurter", s.sources.fx.url)
    try:
        with httpx.Client(transport=transport, timeout=20, headers={"User-Agent": s.limits.web.user_agent}) as c:
            r = c.get(s.sources.fx.url, params={"base": "SGD", "symbols": cur})
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as ex:
        log.warning("fx refresh failed: %s", ex)
        lim.failed("fx", "frankfurter", str(ex)[:80])
        return 0
    n = 0
    for c, per_sgd in (data.get("rates") or {}).items():
        if per_sgd:
            db.execute("INSERT OR REPLACE INTO fx(date, currency, rate) VALUES(?,?,?)", (today, c, 1 / float(per_sgd)))
            n += 1
    return n


def rate(db, currency: str | None, on: str) -> float | None:
    if not currency:
        return None
    c = currency.upper()
    if c == "SGD":
        return 1.0
    oldest = (date.fromisoformat(on) - timedelta(days=7)).isoformat()
    r = db.execute("SELECT rate FROM fx WHERE currency=? AND date<=? AND date>=? ORDER BY date DESC LIMIT 1",
                   (c, on, oldest)).fetchone()
    return r["rate"] if r else None


def to_sgd(db, amount: float | None, currency: str | None, on: str) -> float | None:
    if amount is None:
        return None
    rt = rate(db, currency, on)
    return round(amount * rt, 2) if rt else None
