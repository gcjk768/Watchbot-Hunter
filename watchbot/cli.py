"""watchbot serve | collect | backfill <days> | market | deals | discover | lesson | hello | status | health | purge | test-telegram | watchlist"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from . import config
from .db import connect


def setup_logging(s) -> None:
    (s.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [logging.StreamHandler(), RotatingFileHandler(s.data_dir / "logs" / "watchbot.log", maxBytes=2_000_000,
                                                             backupCount=3)]
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)   # its INFO lines print the bot token in every URL


def context(s):
    from .ratelimit import Limiter
    from .web import Fetcher
    db = connect(s.db_path)
    config.check_paper_lock(s, db)
    lim = Limiter(db, s)
    return db, lim, Fetcher(db, lim, s)


def money(v) -> str:
    return "unknown" if v is None else f"S${v:,.0f}"


def watchlist_table(db) -> str:
    from . import refs
    lines = [f"{'ref':<22}{'brand':<12}{'model':<36}{'retail':>10}  {'retail date':<12}{'eBay SG':>8}  status"]
    for r in refs.watched(db):
        n = db.execute("SELECT COUNT(*) FROM listings WHERE ref=? AND source='ebay' AND ended_at IS NULL",
                       (r["ref"],)).fetchone()[0]
        lines.append(f"{r['ref']:<22}{r['brand']:<12}{r['model'][:35]:<36}{money(r['retail_sgd']):>10}  "
                     f"{r['retail_date'] or '':<12}{n:>8}  {r['facts'].get('status', '')}")
    return "\n".join(lines)


def cmd_collect(s, a, slow: float = 0.0) -> int:
    from .collect import run
    db, lim, f = context(s)
    out = run(s, db, lim, fetcher=f, slow=slow)
    for ref, c in out["per_ref"].items():
        print(f"{ref:<22} eBay results {c['found']:>4}  this ref in SG {c['kept']:>3}  dropped non SG {c['dropped']:>3}  "
              f"new {c['new']:>3}  price changes {c['price']:>3}  ended {c['ended']:>3}")
    print("notes:", "; ".join(out["notes"]) or "none")
    return 0


def cmd_backfill(s, a) -> int:
    """Collect the watchlist slowly. eBay has no history API, so each day of backfill is one collect on that day;
    run it once now and the daily schedule builds the rest."""
    if a.days > 1:
        print(f"Note: eBay offers no listing history, so this records today; {a.days} days of history build up "
              f"from the daily collect. WatchCharts history is loaded when WATCHCHARTS_API_KEY is set.")
    cmd_collect(s, a, slow=30.0)
    db = connect(s.db_path)
    print()
    print(watchlist_table(db))
    return 0


def cmd_status(s, a) -> int:
    from .status import report
    db, lim, _ = context(s)
    print(report(s, db, lim))
    return 0


def cmd_purge(s, a) -> int:
    from . import telegram as tgm
    db, lim, _ = context(s)
    tg = tgm.Telegram(s.bot_token, s, lim)
    rows = tgm.previous_deal_rows(db, s.telegram.chat_id, None)
    print(f"{len(rows)} deal messages up. Removing.")
    print(tgm.delete_posts(db, tg, rows))
    return 0


def cmd_test_telegram(s, a) -> int:
    from . import telegram as tgm
    from .alerts import Alerts
    db, lim, _ = context(s)
    tg = tgm.Telegram(s.bot_token, s, lim)
    print("bot:", tg.call("getMe")["username"])
    mid = tg.send(s.telegram.chat_id, "watchbot test message, deleting it now", thread_id=s.telegram.get("thread_id"))
    tg.call("deleteMessage", chat_id=s.telegram.chat_id, message_id=mid)
    print("channel: posted and deleted")
    if s.telegram.admin_chat_id:
        Alerts(tg, s.telegram.admin_chat_id, s.telegram.chat_id).send("test", "test-telegram worked")
        print("admin chat: pinged")
    else:
        print("admin chat: TELEGRAM_ADMIN_CHAT_ID is empty, alerts are log only")
    return 0


def cmd_watchlist(s, a) -> int:
    from . import refs
    db, _, _ = context(s)
    refs.seed(db, s)
    print(watchlist_table(db))
    return 0


def cmd_serve(s, a) -> int:
    from . import serve
    serve.main(s)
    return 0


def cmd_health(s, a) -> int:
    """Docker healthcheck: the scheduler writes data/heartbeat every minute."""
    import time
    hb = s.data_dir / "heartbeat"
    age = time.time() - float(hb.read_text()) if hb.exists() else 1e9
    print(f"heartbeat {age:.0f} s ago")
    return 0 if age < 600 else 1


def _job(name: str, s) -> int:
    """Run one scheduler job now, posting exactly as the schedule would."""
    from . import serve
    getattr(serve, f"{name}_job")(s)
    return 0


def cmd_market(s, a) -> int:
    from . import serve
    x = serve.Ctx(s)
    texts = serve.market_cards(x)
    x.post(texts) if a.post else print("\n\n".join(texts))
    return 0


def cmd_deals(s, a) -> int:
    from . import serve
    x = serve.Ctx(s)
    texts = serve.deal_cards(x, only_new=False)
    print("\n\n".join(texts) or "no deals clear the floors")
    return 0


def cmd_sample(s, a) -> int:
    """Post N Singapore listings as watch cards, one per reference first. Runs one discovery call when the
    database holds fewer than N open listings (eBay keys missing, or first run)."""
    from . import ai, cards, market, refs, serve, vault
    x = serve.Ctx(s)
    refs.seed(x.db, s)
    q = "SELECT * FROM listings WHERE ended_at IS NULL AND price_sgd>0 ORDER BY last_seen DESC, id DESC"
    if len(x.db.execute(q).fetchall()) < a.n:
        out = ai.discover(s, x.db, x.lim, x.lim.today())
        print(f"discovery: {out['listings']} kept, {out['dropped']} dropped; {out['note']}")
        vault.log_event("🌐", "discovery", f"{out['listings']} Singapore listings kept, {out['dropped']} dropped")
    rows = [dict(r) for r in x.db.execute(q)]
    picked = list({r["ref"]: r for r in reversed(rows)}.values())[: a.n]   # newest per reference
    picked += [r for r in rows if r not in picked][: a.n - len(picked)]
    if not picked:
        print("no Singapore listings found")
        return 1
    mv = market.values(s, x.db, x.lim.today())
    items = [(l, refs.get(x.db, l["ref"]), (mv.get(l["ref"]) or {}).get("value")
              if (mv.get(l["ref"]) or {}).get("label") == "market" else None) for l in picked]
    x.post(cards.listings(items))
    vault.log_event("📨", "listings posted", f"{len(items)} watches")
    print(f"posted {len(items)} watches")
    return 0


def cmd_hello(s, a) -> int:
    """One test card into the bot's topic, with the follow up buttons."""
    import shutil

    from . import cards, refs, serve
    from .status import report
    x = serve.Ctx(s)
    refs.seed(x.db, s)
    n = len(refs.watched(x.db))
    body = "\n".join([
        cards.header("hello", "deployed and listening"), "",
        f"⌚ <b>Watchlist</b> · {n} references, Singapore only",
        f"🔎 eBay SG collect · <i>hourly</i>" + ("" if s.secrets.ebay_client_id else " · 🔴 <i>no EBAY_CLIENT_ID yet</i>"),
        "🌐 Discovery 06:30 · 🎓 lesson 08:00 · 📈 market 08:30",
        f"🤖 claude -p · <code>{cards.esc(s.claude.model)}</code> / <code>{cards.esc(s.claude.discover_model)}</code>"
        + (" · 🟢" if shutil.which(s.claude.binary) else " · 🔴 <i>CLI missing</i>"),
        "🧾 Paper mode · <i>learn first, real buys only after 10 closed paper trades</i>", "",
        "<i>Try /watchhelp, /watchmarket or /watchdeals in this topic.</i>",
        "", f"<blockquote expandable>{cards.esc(report(s, x.db, x.lim))}</blockquote>"])
    x.post([body])
    print("posted")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="watchbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect", help="one collect now: FX, retail, every source, Singapore only")
    b = sub.add_parser("backfill", help="collect the watchlist slowly to build history")
    b.add_argument("days", type=int)
    for name in ("status", "purge", "test-telegram", "watchlist", "serve", "health", "deals", "discover", "lesson",
                 "hello"):
        sub.add_parser(name)
    sub.add_parser("market").add_argument("--post", action="store_true", help="post the card instead of printing")
    sub.add_parser("sample", help="post N Singapore listings as watch cards").add_argument("n", type=int, nargs="?", default=5)
    a = ap.parse_args(argv)
    try:
        s = config.load()
    except config.ConfigError as ex:
        print(ex, file=sys.stderr)
        return 2
    setup_logging(s)
    cmds = {"collect": cmd_collect, "backfill": cmd_backfill, "status": cmd_status, "purge": cmd_purge,
            "test-telegram": cmd_test_telegram, "watchlist": cmd_watchlist, "serve": cmd_serve, "health": cmd_health,
            "market": cmd_market, "deals": cmd_deals, "hello": cmd_hello, "sample": cmd_sample,
            "discover": lambda s, a: _job("discover", s), "lesson": lambda s, a: _job("lesson", s)}
    try:
        return cmds[a.cmd](s, a)
    except config.ConfigError as ex:
        print(ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
