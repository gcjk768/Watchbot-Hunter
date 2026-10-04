"""Telegram cards, same layout as the SG car tracker (@owner_sgcar_bot): `emoji <b>TITLE</b> · subtitle`, then
numbered item cards `emoji <b>n. <a>Name</a></b> · desc 🆕 <i>NEW</i>` with short dot-joined detail lines, method
notes in a collapsed quote at the end. Every dynamic value is escaped."""
from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

# one fixed emoji per section, first word of the title; header() uppercases the rest
SECTION_TITLES = {
    "deals": "💎 Watch deals",
    "listings": "⌚ Watch listings",
    "market": "📈 Watch market",
    "focus": "🎯 Watch focus",
    "lesson": "🎓 Watch lesson",
    "status": "🩺 Watchbot status",
    "watchlist": "📋 Watchlist",
    "news": "📰 Watch news",
    "alert": "🚨 Watchbot alert",
    "hello": "⌚ Watchbot",
}
TAG_EMOJI = {"NEW": "🆕", "DROP": "🟢", "DEAL": "💎", "CHECK": "⚠️"}
DIVIDER = "━━━━━━━━━━━━━━━━"
LIMIT = 4000
CHANNELS = {"local_private": "local private sale", "consignment": "consignment", "dealer_buyback": "dealer buyback",
            "online_platform": "online platform"}


def today() -> str:
    return datetime.now(ZoneInfo("Asia/Singapore")).strftime("%a %d %b %Y")


def esc(v) -> str:
    return escape(str(v), quote=True)


def money(v) -> str:
    return "n/a" if v is None else f"${v:,.0f}"


def delta(value: float, pct: float | None = None) -> str:
    """Signed change with an arrow so the sign is obvious on a phone: ▲1,850 (12.0%)."""
    arrow = "▲" if value > 0 else ("▼" if value < 0 else "•")
    return f"{arrow}{abs(value):,.0f}" + (f" ({abs(pct):.1f}%)" if pct is not None else "")


def mark(change: float, flat: float = 0.02, base: float = 1.0) -> str:
    """Colour for a buyer: cheaper is good."""
    return "⚪" if abs(change) < flat * base else ("🟢" if change < 0 else "🔴")


def dot(*bits) -> str:
    return " · ".join(esc(b) for b in bits if b)


def shop(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def link(url: str, label: str) -> str:
    return f'<a href="{esc(url)}">{esc(label)}</a>'


def buy(url: str) -> str:
    """The purchase link every listing and deal card ends with."""
    return "🛒 " + link(url, f"Buy at {shop(url) or 'listing'}")


def note(text: str) -> str:
    return f"<blockquote expandable>{text}</blockquote>" if text else ""


def header(kind: str, subtitle: str = "") -> str:
    emoji, _, title = SECTION_TITLES[kind].partition(" ")
    return f"{emoji} <b>{esc(title.upper())}</b>" + (f" · {esc(subtitle)}" if subtitle else "")


def card(n: int, title: str, url: str | None, lines, tag: str = "", emoji: str = "⌚", desc: str = "") -> str:
    """`emoji <b>n. linked name</b> · desc 🆕 <i>NEW</i>`, then the caller's emoji-led detail lines."""
    name_html = link(url, title) if url else esc(title)
    head = f"{emoji} <b>{n}. {name_html}</b>" + (f" · {esc(desc)}" if desc else "")
    if tag:
        m = TAG_EMOJI.get(tag.split()[0], "")
        head += f" {m} <i>{esc(tag)}</i>" if m else f" <i>{esc(tag)}</i>"
    return "\n".join([head] + [l for l in lines if l])


def split(head: str, blocks: list[str], footer: str = "") -> list[str]:
    """Header, blocks with blank lines between, the collapsed note last; split under the limit between blocks."""
    msgs, cur = [], head
    for b in [b for b in blocks if b] + ([footer] if footer else []):
        if len(cur) + len(b) + 2 > LIMIT:
            msgs.append(cur)
            cur = b
        else:
            cur += "\n\n" + b
    msgs.append(cur)
    return msgs


def name(ref: dict) -> str:
    return f"{esc(ref.get('brand') or '')} {esc(ref.get('model') or '')}".strip()


def plain_name(ref: dict) -> str:
    return f"{ref.get('brand') or ''} {ref.get('model') or ''}".strip() or ref["ref"]


def _facts(l: dict) -> list:
    return [str(l["year"]) if l.get("year") else None, {1: "full set", 0: "watch only"}.get(l.get("full_set")),
            l.get("condition")]


def _vs(price: float, base: float | None, what: str) -> str:
    if not price or not base:
        return ""
    d = price - base
    return f"{mark(d, base=base)} <i>{esc(delta(d, d / base * 100))} vs {what}</i>"


def listing_block(n: int, l: dict, ref: dict, market_value: float | None, tag: str = "") -> str:
    base, what = (market_value, "market") if market_value else (ref.get("retail_sgd"), "retail")
    p = l["price_sgd"]
    from_search = '"from_search": true' in (l.get("raw_json") or "")
    lines = ["💰 " + dot(money(p), *_facts(l)),
             "📊 " + " · ".join(x for x in [esc(f"Retail {money(ref.get('retail_sgd'))}"),
                                           esc(f"market {money(market_value)}") if market_value else "",
                                           _vs(p, base, what)] if x),
             "🏠 " + dot(shop(l.get("url") or "") or l["source"], l.get("seller_type"))
             + (" · <i>from search</i>" if from_search else ""),
             buy(l["url"]) if l.get("url") else ""]
    return card(n, plain_name(ref), l.get("url"), lines, tag, desc=ref["ref"])


def listings(items: list[tuple[dict, dict, float | None]], new: bool = False, day: str = "") -> list[str]:
    blocks = [listing_block(i, *it, tag="NEW" if new else "") for i, it in enumerate(items, start=1)]
    sub = dot(f"{len(items)} {'new ' if new else ''}in Singapore", day or today())
    return split(header("listings", sub), blocks, note(
        "Asking prices, not sales. 🟢 below and 🔴 above the reference: market value when at least five Singapore "
        "asks exist, otherwise the brand's Singapore retail. Paper mode: learn, do not buy yet."))


def deal_block(n: int, d: dict) -> str:
    l, ref = d["listing"], d["ref"]
    too_good = any(f.startswith("too good") for f in d["flags"])
    lines = ["💰 " + dot(money(l["price_sgd"]), *_facts(l)),
             f"🟢 <b>Net {esc(money(d['net']))}</b> <i>({d['margin_pct']}%)</i> · "
             + dot(f"via {CHANNELS.get(d['channel'], d['channel'])}", f"landed {money(d['landed'])}"),
             "📊 " + " · ".join([esc(f"Market {money(d['market'])}"), esc(f"retail {money(ref.get('retail_sgd'))}"),
                                _vs(l["price_sgd"], d["market"], "market")]),
             "🏠 " + dot(shop(l.get("url") or "") or l["source"], l.get("seller_type")),
             ("⚠️ <i>" + esc("; ".join(d["flags"])) + "</i>") if d["flags"] else "",
             buy(l["url"]) if l.get("url") else ""]
    return card(n, plain_name(ref), l.get("url"), lines, "CHECK" if too_good else "DEAL", emoji="💎", desc=ref["ref"])


def deals(ds: list[dict], day: str = "") -> list[str]:
    return split(header("deals", dot(f"{len(ds)} new in Singapore", day or today())), [deal_block(i, d) for i, d in enumerate(ds, 1)],
                 note("Net = best exit at market value, minus sell fees, minus landed cost (price, delivery, payment, "
                      "insurance, authentication and a service reserve). Prices are asks, not sales. "
                      "Paper mode: learn, do not buy yet."))


def market(refs: list[dict], mv: dict[str, dict], day: str) -> list[str]:
    blocks = []
    for n, r in enumerate(refs, start=1):
        m = mv.get(r["ref"])
        if m:
            move = ""
            if m["move_pct"] is not None:
                move = f" · {mark(m['move_pct'], 1, 1)} <i>{'▼' if m['move_pct'] < 0 else '▲'}{abs(m['move_pct'])}% 7d</i>"
            line = "💰 " + dot(f"Market {money(m['value'])}", f"{m['n']} asks", "thin" if m["label"] != "market" else "") + move
        else:
            line = "⚪ <i>no Singapore asks yet</i>"
        retail = "🏷 " + dot(f"Retail {money(r.get('retail_sgd'))}", r.get("retail_date"))
        blocks.append(card(n, plain_name(r), None, [line, retail], desc=r["ref"]))
    return split(header("market", day), blocks, note(
        "Market = trimmed median of Singapore asking prices from the last 30 days, times 0.95 because asks sit above "
        "sales. Thin = fewer than five asks, shown but never used for deals. 🟢 cheaper, 🔴 dearer over 7 days."))


def _trend_line(r: dict) -> str:
    """Appreciation green, depreciation red: this is about the value of a watch you hold, not the price to pay."""
    t, p = r["trend"], r["premium_pct"]
    if t:
        word = "APPRECIATING" if t["pct"] > 0 else ("DEPRECIATING" if t["pct"] < 0 else "FLAT")
        head = f"{'🟢' if t['pct'] > 0 else '🔴' if t['pct'] < 0 else '⚪'} <b>{word}</b> {esc(delta(t['new'] - t['old'], t['pct']))} <i>since {esc(t['since'])}</i>"
    else:
        head = "⚪ <i>trend needs two days of solid asks</i>"
    vs = f" · {'🟢' if p > 0 else '🔴' if p < 0 else '⚪'} <i>{esc(delta(p))}% vs retail</i>" if p is not None else ""
    return head + vs


def focus_card(f: dict) -> str:
    """One message per focus model: the cheapest open listing, then each reference's market value and trend."""
    l = f["cheapest"]
    if l:
        ref = next(r["ref"] for r in f["refs"] if r["ref"]["ref"] == l["ref"])
        m = next(r["market"] for r in f["refs"] if r["ref"]["ref"] == l["ref"])
        body = ["💸 <b>Cheapest now</b>", listing_block(1, l, ref, m)]
    else:
        body = ["💸 <b>Cheapest now</b>\n⚪ <i>no open Singapore listing yet</i>"]
    blocks = [card(i, plain_name(r["ref"]), None,
                   ["💰 " + dot(f"Market {money(r['market'])}" if r["market"] else "Market thin", f"{r['n']} asks",
                                f"Retail {money(r['ref'].get('retail_sgd'))}"), _trend_line(r)], desc=r["ref"]["ref"])
              for i, r in enumerate(f["refs"], 1)]
    return "\n\n".join([header("focus", f["title"])] + body + ["📊 <b>Value by reference</b>"] + blocks +
                         [note("Asking prices, not sales. Market = trimmed median of 30-day Singapore asks times 0.95, "
                               "needs five asks. Trend compares the latest market value with 30 days ago, or the oldest "
                               "we hold. 🟢 appreciating or above retail, 🔴 depreciating or below: the view of a holder.")])


def lesson(title: str, body: str, action: str, topic: str) -> str:
    return "\n\n".join([header("lesson", topic), f"🎓 <b>{esc(title)}</b>\n{esc(body)}", f"✅ <i>Today:</i> {esc(action)}"])


def news(items: list[dict]) -> list[str]:
    blocks = [card(i, n["headline"], n.get("url"), [esc(n["gist"])], "NEW", emoji="📰", desc=n["ref_or_brand"])
              for i, n in enumerate(items, start=1)]
    return split(header("news", f"{len(items)} new"), blocks)


def status(text: str) -> str:
    return f"{header('status', 'budgets and data')}\n\n{note(esc(text))}"


def alert(kind: str, text: str) -> str:
    return f"{header('alert', kind)}\n\n{esc(text)}"
