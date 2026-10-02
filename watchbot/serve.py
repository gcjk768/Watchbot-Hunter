"""The container: APScheduler jobs (hourly collect + new deal cards, daily discovery, lesson and market card) and
the Telegram command listener. Every job opens its own SQLite connection, logs to the vault, and alerts the admin
chat on failure instead of crashing."""
from __future__ import annotations

import logging
import threading
import time
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import cards, market, refs as refsmod, vault
from .alerts import Alerts
from .telegram import Telegram

log = logging.getLogger(__name__)
BUTTONS = [("📈 Market", "watchmarket"), ("💎 Deals", "watchdeals")]
CALLBACKS = {"watchmarket", "watchdeals", "watchstatus"}   # cheap commands only, never a Claude call
HELP = "\n".join([
    cards.header("hello", "Singapore watch market"), "",
    "/watchmarket · market value per watched reference",
    "/watchdeals · open deals that clear your margin",
    "/watchlist · the watchlist with retail prices",
    "/watchadd <code>REF Brand Model</code> · watch a reference",
    "/watchdel <code>REF</code> · stop watching it",
    "/watchstatus · budgets, cooldowns, data held",
    "<i>Hourly eBay collect, daily discovery 06:30, lesson 08:00, market card 08:30. New deals post the moment they appear.</i>"])


class Ctx:
    """One job's or the listener's own connection, limiter, fetcher, Telegram client and alerts."""

    def __init__(self, s):
        from .cli import context
        self.s = s
        self.db, self.lim, self.fetcher = context(s)
        self.tg = Telegram(s.bot_token, s, self.lim) if s.bot_token else None
        self.alerts = Alerts(self.tg, s.telegram.admin_chat_id, s.telegram.chat_id)

    def post(self, texts: list[str], buttons=BUTTONS) -> None:
        if self.tg:
            self.tg.post(texts, buttons)
        else:
            log.info("no TELEGRAM_BOT_TOKEN; would post:\n%s", "\n\n".join(texts))


def job(name: str):
    def deco(fn):
        @wraps(fn)
        def run(s, *a, **kw):
            x = Ctx(s)
            try:
                return fn(x, *a, **kw)
            except Exception as ex:   # a job never kills the scheduler; the admin hears about it once
                log.exception("%s failed", name)
                vault.log_event("❌", f"{name} failed", f"{type(ex).__name__}: {ex}"[:300])
                x.alerts.send(name, f"{type(ex).__name__}: {ex}"[:1000])
            finally:
                x.db.close()
        return run
    return deco


def deal_cards(x, only_new: bool, run_id: str = "manual") -> list[str]:
    mv = market.values(x.s, x.db, x.lim.today())
    ds = market.find_deals(x.s, x.db, mv)
    if only_new:
        ds = market.new_deals(x.db, ds, run_id, x.lim.today())
    ds = ds[: x.s.run.deals_per_alert]
    for d in ds:
        vault.log_event("💎", "deal found", f"{d['ref']['ref']} {cards.money(d['listing']['price_sgd'])}, net "
                        f"{cards.money(d['net'])} ({d['margin_pct']}%)", vault.watch_note(d["ref"]))
        vault.history(vault.watch_note(d["ref"]), f"deal {cards.money(d['listing']['price_sgd'])} vs market "
                      f"{cards.money(d['market'])}, net {cards.money(d['net'])}, {d['listing'].get('url') or ''}")
    return cards.deals(ds) if ds else []


def market_cards(x) -> list[str]:
    mv = market.values(x.s, x.db, x.lim.today())
    return cards.market(refsmod.watched(x.db), mv, x.lim.today())


@job("collect")
def collect_job(x) -> None:
    from .collect import run
    out = run(x.s, x.db, x.lim, fetcher=x.fetcher)
    new = sum(c["new"] for c in out["per_ref"].values())
    vault.log_event("🔎", "collect", f"{new} new listings; " + ("; ".join(out["notes"]) or "no notes"))
    texts = deal_cards(x, only_new=True, run_id=out["run_id"])
    if texts:
        x.post(texts)
        vault.log_event("📨", "deal alert sent", f"{len(texts)} message(s)")


@job("discover")
def discover_job(x) -> None:
    from . import ai
    out = ai.discover(x.s, x.db, x.lim, x.lim.today())
    vault.log_event("🌐", "discovery", f"{out['listings']} Singapore listings kept, {out['dropped']} dropped, "
                    f"{len(out['news'])} new news; {out['note']}"[:400])
    if out["news"]:
        x.post(cards.news(out["news"]), buttons=None)
    texts = deal_cards(x, only_new=True, run_id=f"discover-{x.lim.today()}")
    if texts:
        x.post(texts)


@job("lesson")
def lesson_job(x) -> None:
    from . import ai
    l = ai.lesson(x.s, x.db, x.lim, x.lim.today())
    x.post([cards.lesson(l["title"], l["body"], l["action"], l["topic"])], buttons=None)
    vault.log_event("🎓", "lesson posted", f"{l['topic']}, cycle {l['cycle']}: {l['title']}")


@job("market")
def market_job(x) -> None:
    x.post(market_cards(x))
    vault.log_event("📈", "market card posted")
    vault.write_home()


class Bot:
    """Owner only, in the private chat or inside the bot's own forum topic. Unique /watch* names (shared group)."""

    def __init__(self, x):
        self.x, self.s = x, x.s

    def allowed(self, m: dict) -> bool:
        if m.get("from", {}).get("id") != self.s.telegram.owner_user_id:
            return False
        chat = m.get("chat", {})
        if chat.get("type") == "private":
            return True
        thread = self.s.telegram.get("thread_id")
        return str(chat.get("id")) == str(self.s.telegram.chat_id) and (not thread or m.get("message_thread_id") == thread)

    def handle(self, u: dict) -> None:
        if cq := u.get("callback_query"):
            msg = cq.get("message") or {}
            ok = cq.get("data") in CALLBACKS and self.allowed({**msg, "from": cq.get("from", {})})
            self.x.tg.call("answerCallbackQuery", callback_query_id=cq["id"])   # always stop the spinner
            if ok:
                self.run(cq["data"], "", msg)
            return
        m = u.get("message") or {}
        text = (m.get("text") or "").strip()
        if not text.startswith("/") or not self.allowed(m):
            return
        cmd, _, arg = text.partition(" ")
        self.run(cmd[1:].split("@")[0].lower(), arg.strip(), m)

    def run(self, cmd: str, arg: str, m: dict) -> None:
        fn = getattr(self, "cmd_" + cmd, None)
        if not fn:
            return   # other bots' commands in a shared group: stay silent
        vault.log_event("💬", "command", f"/{cmd} {arg}".strip())
        texts = fn(arg)
        chat, thread = m["chat"]["id"], m.get("message_thread_id")
        for i, t in enumerate(texts):
            self.x.tg.send(chat, t, thread_id=thread, buttons=BUTTONS if i == len(texts) - 1 else None)

    def cmd_watchhelp(self, arg):
        return [HELP]

    cmd_start = cmd_watchhelp

    def cmd_watchmarket(self, arg):
        return market_cards(self.x)

    def cmd_watchdeals(self, arg):
        return deal_cards(self.x, only_new=False) or [cards.header("deals", "none right now") + "\n\n<i>No open "
                                                      "Singapore listing clears your margin and profit floors.</i>"]

    def cmd_watchstatus(self, arg):
        from .status import report
        return [cards.status(report(self.s, self.x.db, self.x.lim))]

    def cmd_watchlist(self, arg):
        rows = refsmod.watched(self.x.db)
        blocks = [f"⌚ <b>{cards.name(r)}</b> · <code>{cards.esc(r['ref'])}</code>\n🏷 retail {cards.money(r['retail_sgd'])}"
                  for r in rows]
        return cards.split(cards.header("watchlist", f"{len(rows)} references"), blocks)

    def cmd_watchadd(self, arg):
        parts = arg.split(maxsplit=2)
        if not parts:
            return ["Use /watchadd <code>REF Brand Model</code>, for example /watchadd <code>126500LN Rolex Daytona</code>"]
        refsmod.add(self.x.db, parts[0], *(parts[1:] + ["", ""])[:2])
        vault.log_event("➕", "watching", parts[0])
        return [f"🆕 Watching <code>{cards.esc(parts[0])}</code>. It joins the next hourly collect."]

    def cmd_watchdel(self, arg):
        if not arg:
            return ["Use /watchdel <code>REF</code>"]
        ok = refsmod.remove(self.x.db, arg.split()[0])
        vault.log_event("❌", "stopped watching", arg.split()[0])
        return [f"❌ Stopped watching <code>{cards.esc(arg.split()[0])}</code>." if ok else "Not on the watchlist."]


def heartbeat(s) -> None:
    (s.data_dir / "heartbeat").write_text(str(time.time()))


def main(s) -> None:
    tz = s.schedule.timezone
    sch = BackgroundScheduler(timezone=tz, job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 900})
    for name, fn in (("collect", collect_job), ("discover", discover_job), ("lesson", lesson_job), ("market", market_job)):
        sch.add_job(fn, CronTrigger.from_crontab(s.schedule[f"{name}_cron"], timezone=tz), args=[s], id=name)
    sch.add_job(heartbeat, "interval", minutes=1, args=[s], id="heartbeat")
    sch.start()
    heartbeat(s)
    vault.log_event("🚀", "watchbot started", f"jobs: {', '.join(j.id for j in sch.get_jobs())}")
    vault.write_home()
    x = Ctx(s)
    if not x.tg:
        log.warning("no TELEGRAM_BOT_TOKEN: jobs log their cards, the listener is off")
        threading.Event().wait()
    x.tg.poll(x.db, Bot(x).handle)
