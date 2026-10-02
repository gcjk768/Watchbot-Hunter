"""Plain text status: budgets used today, cooldowns, data held, paper mode."""
from __future__ import annotations

from . import config


def report(s, db, lim) -> str:
    out = [f"day {lim.today()} (Asia/Singapore)", "budgets used today:"]
    for b in ("web", "chrono24", "ebay", "watchcharts", "fx", "claude", "telegram"):
        cap = lim.daily_cap(b)
        out.append(f"  {b:<12}{lim.used(b):>5} of {'no cap' if cap == float('inf') else int(cap)}")
    cds = db.execute("SELECT bucket, key, cooldown_until, cooldown_reason FROM rate_state WHERE cooldown_until>?",
                     (lim.clock(),)).fetchall()
    out.append("cooldowns: " + (", ".join(f"{r['bucket']} {r['key']} ({r['cooldown_reason']})" for r in cds) or "none"))
    n = lambda q: db.execute(q).fetchone()[0]
    out.append(f"refs watched {n('SELECT COUNT(*) FROM refs WHERE watched=1')}, "
               f"open listings {n('SELECT COUNT(*) FROM listings WHERE ended_at IS NULL')}, "
               f"ended {n('SELECT COUNT(*) FROM listings WHERE ended_at IS NOT NULL')}")
    out.append(f"paper mode {'on' if s.me.paper_mode else 'off'}, closed paper trades {config.closed_paper_trades(db)} "
               f"of {s.me.paper_min_trades} needed")
    last = db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if last:
        out.append(f"last run {last['id']} {last['status']}: {last['note'] or ''}")
    return "\n".join(out)
