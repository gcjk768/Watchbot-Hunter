"""Telegram cards in the fleet style: HTML, emoji + bold title header, one block per item, every dynamic value escaped."""
from __future__ import annotations

from html import escape
from urllib.parse import urlparse

SECTION_TITLES = {
    "deals": "💎 <b>WATCH DEALS</b>",
    "listings": "🔎 <b>WATCH LISTINGS</b>",
    "market": "📈 <b>WATCH MARKET</b>",
    "lesson": "🎓 <b>WATCH LESSON</b>",
    "status": "🩺 <b>WATCHBOT STATUS</b>",
    "watchlist": "📋 <b>WATCHLIST</b>",
    "news": "📰 <b>WATCH NEWS</b>",
    "alert": "🚨 <b>WATCHBOT ALERT</b>",
    "hello": "⌚ <b>WATCHBOT</b>",
}
DIVIDER = "━━━━━━━━━━━━━━━━"
LIMIT = 4000
CHANNELS = {"local_private": "local private sale", "consignment": "consignment", "dealer_buyback": "dealer buyback",
            "online_platform": "online platform"}


def esc(v) -> str:
    return escape(str(v), quote=True)


def money(v) -> str:
    return "unknown" if v is None else f"S${v:,.0f}"


def shop(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def buy(url: str) -> str:
    """The purchase link line every listing and deal card ends with."""
    return "🛒 " + link(url, f"Buy at {shop(url) or 'listing'}")


def link(url: str, label: str) -> str:
    return f'<a href="{esc(url)}">{esc(label)}</a>'


def header(kind: str, subtitle: str) -> str:
    return f"{SECTION_TITLES[kind]} · {esc(subtitle)}"


def split(head: str, blocks: list[str], footer: str = "") -> list[str]:
    """Join blocks under Telegram's limit, splitting only between blocks."""
    msgs, cur = [], head
    for b in blocks + ([footer] if footer else []):
        if len(cur) + len(b) + 2 > LIMIT:
            msgs.append(cur)
            cur = b
        else:
            cur += "\n\n" + b
    msgs.append(cur)
    return msgs


def name(ref: dict) -> str:
    return f"{esc(ref.get('brand') or '')} {esc(ref.get('model') or '')}".strip()


def deal_block(d: dict) -> str:
    l, ref = d["listing"], d["ref"]
    marker = "⚠️" if any(f.startswith("too good") for f in d["flags"]) else "🆕"
    bits = [l.get("condition"), str(l["year"]) if l.get("year") else None,
            {1: "full set", 0: "watch only"}.get(l.get("full_set")), l.get("seller_type")]
    lines = [f"{marker} <b>{name(ref)}</b> · <code>{esc(ref['ref'])}</code>",
             f"💰 {money(l['price_sgd'])} asking · market {money(d['market'])} · retail {money(ref.get('retail_sgd'))}",
             f"🟢 net {money(d['net'])} <i>({d['margin_pct']}%)</i> via {esc(CHANNELS.get(d['channel'], d['channel']))}"
             f" · landed {money(d['landed'])}"]
    if any(bits):
        lines.append("🏷 " + " · ".join(esc(b) for b in bits if b))
    if d["flags"]:
        lines.append("⚠️ <i>" + esc("; ".join(d["flags"])) + "</i>")
    if l.get("url"):
        lines.append(buy(l["url"]))
    return "\n".join(lines)


def deals(ds: list[dict]) -> list[str]:
    head = header("deals", f"{len(ds)} new in Singapore")
    foot = ("<blockquote expandable>Net = best exit at market value, minus sell fees, minus landed cost (price, "
            "delivery, payment, insurance, authentication and a service reserve). Prices are asks, not sales. "
            "Paper mode: learn, do not buy yet.</blockquote>")
    return split(head, [deal_block(d) for d in ds], foot)


def market(refs: list[dict], mv: dict[str, dict], day: str) -> list[str]:
    blocks = []
    for r in refs:
        m = mv.get(r["ref"])
        lines = [f"⌚ <b>{name(r)}</b> · <code>{esc(r['ref'])}</code>"]
        if m:
            mv_txt = ""
            if m["move_pct"] is not None:
                mk = "⚪" if abs(m["move_pct"]) < 1 else ("🟢" if m["move_pct"] < 0 else "🔴")
                arrow = "▼" if m["move_pct"] < 0 else "▲"
                mv_txt = f" · {mk} <i>{arrow}{abs(m['move_pct'])}% 7d</i>"
            thin = " <i>(thin)</i>" if m["label"] != "market" else ""
            lines.append(f"💰 market {money(m['value'])}{thin} · {m['n']} asks{mv_txt}")
        else:
            lines.append("⚪ <i>no Singapore asks yet</i>")
        lines.append(f"🏷 retail {money(r.get('retail_sgd'))}" + (f" <i>({esc(r['retail_date'])})</i>" if r.get("retail_date") else ""))
        blocks.append("\n".join(lines))
    return split(header("market", day), blocks)


def lesson(title: str, body: str, action: str, topic: str) -> str:
    return "\n".join([header("lesson", topic), "", f"<b>{esc(title)}</b>", esc(body), "", f"✅ <i>Today:</i> {esc(action)}"])


def news(items: list[dict]) -> list[str]:
    blocks = [f"📰 <b>{esc(n['headline'])}</b> · {esc(n['ref_or_brand'])}\n{esc(n['gist'])}"
              + (f"\n🔗 {link(n['url'], 'Source')}" if n.get("url") else "") for n in items]
    return split(header("news", f"{len(items)} new"), blocks)


def status(text: str) -> str:
    return f"{header('status', 'budgets and data')}\n\n<blockquote expandable>{esc(text)}</blockquote>"


def alert(kind: str, text: str) -> str:
    return f"{header('alert', kind)}\n\n{esc(text)}"


def listing_block(l: dict, ref: dict, market_value: float | None) -> str:
    """One Singapore listing: price against market (when solid) or retail, marker good/bad for a buyer."""
    p, base, what = l["price_sgd"], market_value, "market"
    if not base:
        base, what = ref.get("retail_sgd"), "retail"
    vs = ""
    if p and base:
        pct = round((p / base - 1) * 100)
        mk = "⚪" if abs(pct) < 2 else ("🟢" if pct < 0 else "🔴")
        vs = f" · {mk} <i>{'▼' if pct < 0 else '▲'}{abs(pct)}% vs {what}</i>"
    bits = [l.get("condition"), str(l["year"]) if l.get("year") else None,
            {1: "full set", 0: "watch only"}.get(l.get("full_set")), l.get("seller_type")]
    raw = l.get("raw_json") or ""
    lines = [f"⌚ <b>{name(ref)}</b> · <code>{esc(ref['ref'])}</code>",
             f"💰 {money(p)} asking · retail {money(ref.get('retail_sgd'))}{vs}"]
    if any(bits):
        lines.append("🏷 " + " · ".join(esc(b) for b in bits if b))
    site = shop(l.get("url") or "") or l["source"]
    lines.append(f"🏠 {esc(site)}" + (" · <i>from search</i>" if '"from_search": true' in raw else ""))
    if l.get("url"):
        lines.append(buy(l["url"]))
    return "\n".join(lines)


def listings(items: list[tuple[dict, dict, float | None]], new: bool = False) -> list[str]:
    blocks = [("🆕 " if new else "") + listing_block(*i) for i in items]
    return split(header("listings", f"{len(items)} {'new ' if new else ''}in Singapore"), blocks,
                 "<blockquote expandable>Asking prices, not sales. 🟢 below and 🔴 above the reference price. "
                 "Paper mode: learn, do not buy yet.</blockquote>")
