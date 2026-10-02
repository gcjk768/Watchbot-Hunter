"""watchbot collect | backfill <days> | deals | lesson | serve | status | purge | test-telegram | vault rebuild"""
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
    mid = tg.send(s.telegram.chat_id, "watchbot test message, deleting it now")
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="watchbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect", help="one collect now: FX, retail, every source, Singapore only")
    b = sub.add_parser("backfill", help="collect the watchlist slowly to build history")
    b.add_argument("days", type=int)
    for name in ("status", "purge", "test-telegram", "watchlist"):
        sub.add_parser(name)
    a = ap.parse_args(argv)
    try:
        s = config.load()
    except config.ConfigError as ex:
        print(ex, file=sys.stderr)
        return 2
    setup_logging(s)
    cmds = {"collect": cmd_collect, "backfill": cmd_backfill, "status": cmd_status, "purge": cmd_purge,
            "test-telegram": cmd_test_telegram, "watchlist": cmd_watchlist}
    try:
        return cmds[a.cmd](s, a)
    except config.ConfigError as ex:
        print(ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
