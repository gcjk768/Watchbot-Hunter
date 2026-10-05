"""Official Singapore list prices, labelled "retail" with the date.

The figures verified during the build live in refdata.yaml. On the NAS this module re-reads each retail_url
through the polite fetcher (never_fetch, terms, robots.txt first) at most once a week and takes a price only when
the page states it in SGD (JSON LD offers, or product price meta tags). Anything else keeps the stored figure.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from selectolax.lexbor import LexborHTMLParser as HTMLParser   # selectolax 1.0 removed the Modest backend

from ..ratelimit import LimitError
from ..web import Blocked

log = logging.getLogger(__name__)
REFRESH_DAYS = 7


def _num(v) -> float | None:
    try:
        return float(re.sub(r"[^\d.]", "", str(v))) if re.search(r"\d", str(v)) else None
    except ValueError:
        return None


def _offers(node):
    if isinstance(node, list):
        for n in node:
            yield from _offers(n)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _offers(node["@graph"])
        offers = node.get("offers")
        if offers:
            for o in offers if isinstance(offers, list) else [offers]:
                if isinstance(o, dict):
                    yield o
                    if isinstance(o.get("priceSpecification"), dict):
                        yield o["priceSpecification"]


def parse_price_sgd(html: str) -> float | None:
    """The SGD list price a product page states, or None. Never guesses the currency."""
    tree = HTMLParser(html)
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text() or "")
        except (json.JSONDecodeError, ValueError):
            continue
        for o in _offers(data):
            if str(o.get("priceCurrency", "")).upper() == "SGD":
                p = _num(o.get("price") or o.get("lowPrice"))
                if p:
                    return p
    cur = tree.css_first('meta[property="product:price:currency"], meta[itemprop="priceCurrency"], '
                         'meta[property="og:price:currency"]')
    amt = tree.css_first('meta[property="product:price:amount"], meta[itemprop="price"], meta[property="og:price:amount"]')
    if cur and amt and (cur.attributes.get("content") or "").upper() == "SGD":
        return _num(amt.attributes.get("content"))
    return None


def refresh(db, fetcher, today: str, refs: list[str] | None = None) -> dict[str, str]:
    """Re-read due retail pages. Returns ref -> what happened, for the run log."""
    out = {}
    cutoff = (date.fromisoformat(today) - timedelta(days=REFRESH_DAYS)).isoformat()
    rows = db.execute("SELECT ref, retail_url, retail_date, retail_sgd FROM refs WHERE retail_url IS NOT NULL").fetchall()
    for r in rows:
        if refs and r["ref"] not in refs:
            continue
        checked = db.execute("SELECT checked_at FROM rules WHERE key=?", (f"retail_check:{r['ref']}",)).fetchone()
        if checked and checked["checked_at"] >= cutoff:
            continue
        try:
            page = fetcher.get(r["retail_url"])
        except Blocked as ex:
            out[r["ref"]] = f"not fetched: {ex}"
            _mark(db, r["ref"], r["retail_url"], today, str(ex))
            continue
        except LimitError as ex:
            out[r["ref"]] = f"skipped: {ex}"
            break
        price = parse_price_sgd(page.text) if page.status == 200 else None
        if price:
            if price != r["retail_sgd"]:
                out[r["ref"]] = f"retail {r['retail_sgd']} -> {price}"
            db.execute("UPDATE refs SET retail_sgd=?, retail_date=? WHERE ref=?", (price, today, r["ref"]))
            _mark(db, r["ref"], r["retail_url"], today, "ok")
        else:
            out[r["ref"]] = f"no SGD price on the page (HTTP {page.status}), kept {r['retail_sgd']} from {r['retail_date']}"
            _mark(db, r["ref"], r["retail_url"], today, "no price")
    return out


def _mark(db, ref, url, today, note):
    db.execute("INSERT OR REPLACE INTO rules(key, value_json, url, checked_at) VALUES(?,?,?,?)",
               (f"retail_check:{ref}", json.dumps({"note": note}), url, today))
