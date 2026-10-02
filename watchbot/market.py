"""Market value per reference from Singapore asking prices, and the deal maths after every cost.

Market value = trimmed median of the asking prices (SGD) seen in the last comparables_days, times
asking_to_sale_discount, since asks sit above what watches actually sell for. Labelled "market" with at least
min_comparables asks, "thin" below that (shown, never used for deals).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from statistics import median

from . import refs as refsmod


def trimmed_median(prices: list[float], trim_pct: float) -> float:
    p = sorted(prices)
    k = int(len(p) * trim_pct / 100)
    return median(p[k:len(p) - k] if len(p) > 2 * k else p)


def values(s, db, today: str) -> dict[str, dict]:
    """Compute and store today's value for every watched reference. ref -> {value, label, n, move_pct}."""
    since = (date.fromisoformat(today) - timedelta(days=s.run.comparables_days)).isoformat()
    week_ago = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    out = {}
    for r in refsmod.watched(db):
        prices = [row[0] for row in db.execute(
            "SELECT price_sgd FROM listings WHERE ref=? AND price_sgd>0 AND last_seen>=?", (r["ref"], since))]
        if not prices:
            continue
        value = round(trimmed_median(prices, s.run.trim_pct) * s.run.asking_to_sale_discount)
        label = "market" if len(prices) >= s.run.min_comparables else "thin"
        db.execute("INSERT OR REPLACE INTO market_daily(ref, date, condition, set_type, value_sgd, label, n_comparables) "
                   "VALUES(?,?,'any','any',?,?,?)", (r["ref"], today, value, label, len(prices)))
        prev = db.execute("SELECT value_sgd FROM market_daily WHERE ref=? AND date<=? AND label='market' "
                          "ORDER BY date DESC LIMIT 1", (r["ref"], week_ago)).fetchone()
        move = round((value / prev[0] - 1) * 100, 1) if prev and prev[0] else None
        out[r["ref"]] = {"value": value, "label": label, "n": len(prices), "move_pct": move}
    return out


def evaluate(s, listing: dict, ref: dict, market_value: float) -> dict:
    """Landed cost, best exit channel and net result for one listing. All SGD."""
    c, p = s.costs, listing["price_sgd"]
    raw = json.loads(listing.get("raw_json") or "{}")
    ship = raw.get("shipping")
    ship_sgd = ship * p / listing["price"] if ship and listing.get("price") else c.local_delivery_sgd
    age = date.today().year - listing["year"] if listing.get("year") else None
    needs_service = raw.get("service_record") is not True and (age is None or age >= s.run.service_age_years)
    reserve = c.service_reserve_sgd.get(ref.get("brand") or "", c.service_reserve_sgd.default) if needs_service else 0
    costs = {"price": p, "delivery": round(ship_sgd), "payment": round(p * c.payment_fee_pct / 100),
             "insurance": round(p * c.insurance_pct / 100), "authentication": c.authentication_sgd, "service": reserve}
    landed = sum(costs.values())
    exits = {}
    for ch in s.me.sell_channel:
        sale = market_value * (1 - c.dealer_buyback_discount_pct / 100) if ch == "dealer_buyback" else market_value
        exits[ch] = sale * (1 - c.sell_fees_pct.get(ch, 0) / 100)
    channel = max(exits, key=exits.get)
    net = exits[channel] - landed
    flags = []
    if p < market_value * (1 - s.run.too_good_pct / 100):
        flags.append("too good: far below market, a warning sign")
    if listing.get("full_set") == 0:
        flags.append("watch only, no box and papers")
    if listing.get("seller_type") == "private":
        fb = json.loads(listing.get("feedback_json") or "{}")
        if fb.get("score", 0) < 10:
            flags.append("private seller with little feedback")
    return {"landed": round(landed), "costs": costs, "channel": channel, "exit": round(exits[channel]),
            "net": round(net), "margin_pct": round(net / landed * 100, 1), "flags": flags,
            "below_market_pct": round((1 - p / market_value) * 100, 1)}


def find_deals(s, db, mv: dict[str, dict]) -> list[dict]:
    """Open listings seen in the last 3 days that clear the margin and profit floors on a solid market value, best first.
    Too good to be true listings are kept so the card can warn about them."""
    out = []
    for r in refsmod.watched(db):
        m = mv.get(r["ref"])
        if not m or m["label"] != "market":
            continue
        for l in db.execute("SELECT * FROM listings WHERE ref=? AND ended_at IS NULL AND price_sgd>0 "
                            "AND last_seen>=date('now', '-3 day')", (r["ref"],)):
            l = dict(l)
            if l["price_sgd"] > s.me.budget_max_sgd:
                continue
            e = evaluate(s, l, r, m["value"])
            if e["net"] >= s.me.min_net_profit_sgd and e["margin_pct"] >= s.me.min_net_margin_pct:
                out.append({"listing": l, "ref": r, "market": m["value"], **e})
    return sorted(out, key=lambda d: d["margin_pct"], reverse=True)


def new_deals(db, deals: list[dict], run_id: str, today: str) -> list[dict]:
    """Only deals never alerted before (one row per listing in the deals table), recorded as they pass."""
    fresh = []
    for d in deals:
        lid = d["listing"]["id"]
        if db.execute("SELECT 1 FROM deals WHERE listing_id=?", (lid,)).fetchone():
            continue
        db.execute("INSERT INTO deals(listing_id, run_id, costs_json, score, flags_json, label, created_at) "
                   "VALUES(?,?,?,?,?,?,?)", (lid, run_id, json.dumps(d["costs"]), d["margin_pct"],
                                             json.dumps(d["flags"]), "asking", today))
        fresh.append(d)
    return fresh
