"""The two claude -p jobs: daily discovery (web search for Singapore listings and news) and the daily lesson."""
from __future__ import annotations

import hashlib
import json
import logging
import re

from . import claude, collect, refs as refsmod, region, textcheck, vault
from .sources.base import Listing, mentions_ref

FOREIGN_LOCALE = re.compile(r"/(?:[a-z]{2}-(?!sg/)[a-z]{2})(?:/|$)", re.I)   # /en-my/, /en-vn/: another country's price


def buyable(url: str, ref: dict) -> bool:
    """A discovered link is kept only when it is a product page for this exact reference in Singapore: the URL names
    the reference (category pages and look-alike references do not) and is not another country's locale."""
    return bool(url) and mentions_ref(url, ref["ref"], ref.get("aliases") or ()) and not FOREIGN_LOCALE.search(url)

log = logging.getLogger(__name__)
TOPICS = ["reading a reference number", "box and papers and why sets sell faster", "service intervals and costs",
          "authorised dealer, grey market and pre owned", "warning signs of a fake and how to get a watch checked",
          "liquidity: which references sell fast in Singapore", "condition grades: unworn, excellent, good",
          "selling channels and their fees in Singapore", "retail price increases and what they do to used prices",
          "GST and when trading becomes taxable income", "bracelets, stretch and polishing", "discontinued references"]


def _prompt(s, name: str) -> tuple[str, dict]:
    p = s.root / "prompts"
    return str(p / f"{name}_system.md"), json.loads((p / f"{name}_schema.json").read_text(encoding="utf-8"))


def discover(s, db, lim, today: str) -> dict:
    """One web search call for every watched reference. Listings pass the Singapore filter, news is stored once.
    Returns {"listings": kept count, "news": [new news rows], "note": run_note}."""
    system, schema = _prompt(s, "discover")
    watched = refsmod.watched(db)
    stdin = {"today": today, "country": "Singapore",
             "refs": [{"brand": r["brand"], "model": r["model"], "ref": r["ref"], "aliases": r["aliases"]} for r in watched],
             "allowed_domains": [".sg sites", "ebay.com.sg", *(s.rules.get("sg_dealers") or {}).keys()],
             "never_fetch_domains": list(s.never_fetch_domains),
             "already_done": vault.recent(1500)}
    out = claude.call(s, lim, system, stdin, schema, web=True, model=s.claude.discover_model)
    known = {r["ref"]: r for r in watched}
    found = []
    for x in out.get("listings") or []:
        if x.get("ref") not in known or not x.get("price") or not buyable(x.get("url") or "", known[x["ref"]]):
            continue
        sid = hashlib.sha1(f"{x['ref']}|{x.get('url') or x.get('title')}".encode()).hexdigest()[:16]
        found.append(Listing(source="discover", source_id=sid, ref=x["ref"], title=x.get("title") or "",
                             price=float(x["price"]), currency=x.get("currency") or "SGD", url=x.get("url") or "",
                             site=x.get("site") or "", condition=x.get("condition"), year=x.get("year"),
                             full_set=x.get("full_set"), seller_type=x.get("seller_type"),
                             seller_country=x.get("seller_country"), item_country=x.get("seller_country"),
                             from_search=bool(x.get("from_search"))))
    kept, dropped = region.keep_sg(found, s)
    for l in kept:
        collect.upsert(db, l, today)
    news = []
    for n in out.get("news") or []:
        if n.get("url") and db.execute("INSERT OR IGNORE INTO news(ref_or_brand, headline, gist, url, date, found_at) "
                                       "VALUES(?,?,?,?,?,?)", (n["ref_or_brand"], textcheck.undash(n["headline"]),
                                                               textcheck.undash(n["gist"]), n["url"], n.get("date"),
                                                               today)).rowcount:
            news.append(n)
    return {"listings": len(kept), "dropped": len(dropped), "news": news, "note": out.get("run_note", "")}


def lesson(s, db, lim, today: str) -> dict:
    """Next topic in the rotation, deeper each cycle. Raises claude.ClaudeFailure or ValueError (rule check)."""
    system, schema = _prompt(s, "lesson")
    n = db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
    topic, cycle = TOPICS[n % len(TOPICS)], n // len(TOPICS) + 1
    refs = [{"brand": r["brand"], "model": r["model"], "ref": r["ref"], "retail_sgd": r["retail_sgd"]}
            for r in refsmod.watched(db)]
    fees = {k: v for k, v in s.costs.sell_fees_pct.items()}
    numbers = [cycle, s.costs.authentication_sgd, s.rules.get("tax", {}).get("gst_rate_pct", {}).get("value", 9),
               *[r["retail_sgd"] for r in refs if r["retail_sgd"]], *fees.values(),
               *s.costs.service_reserve_sgd.__dict__.values()]
    numbers = [f"{x:,.0f}" if isinstance(x, float) else str(x) for x in numbers]
    # Reference numbers and model names (126610LN, Black Bay 58) are facts the bot itself supplies, so the lesson may
    # quote their digits; only invented prices, fees and counts are rejected by the rule check.
    numbers += [str(r[f]) for r in refs for f in ("ref", "model", "brand") if any(c.isdigit() for c in str(r[f]))]
    stdin = {"topic": topic, "cycle": cycle, "references": refs, "sell_fees_pct": fees,
             "service_reserve_sgd": dict(s.costs.service_reserve_sgd.items()),
             "authentication_sgd": s.costs.authentication_sgd, "allowed_numbers": numbers,
             "already_taught": [r[0] for r in db.execute("SELECT title FROM lessons ORDER BY id DESC LIMIT 30")]}
    out = claude.call(s, lim, system, stdin, schema)
    out = {k: textcheck.undash(v) for k, v in out.items()}
    bad = [p for k in ("title", "body", "action") for p in textcheck.problems(out[k], numbers)]
    if bad:
        raise ValueError("lesson failed the rule check: " + "; ".join(bad))
    db.execute("INSERT INTO lessons(topic, cycle, title, body_json, posted_at) VALUES(?,?,?,?,?)",
               (topic, cycle, out["title"], json.dumps(out, ensure_ascii=False), today))
    return {**out, "topic": topic, "cycle": cycle}



def ask(s, db, lim, question: str, context: str) -> str:
    """One question about the owner's own tracker data (listings, market values, deals). Claude answers from `context`
    only; the same rule check as the lesson applies (no figure that is not in the data, no promises, no links).
    Raises claude.ClaudeFailure or ValueError (rule check)."""
    system, schema = _prompt(s, "ask")
    out = claude.call(s, lim, system, {"question": question[:500], "data": context[:9000]}, schema, bucket="ask")
    answer = textcheck.undash(out["answer"])
    bad = textcheck.problems(answer, [context, question])
    if bad:
        raise ValueError("answer failed the rule check: " + "; ".join(bad))
    return answer
