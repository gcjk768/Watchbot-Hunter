"""Telegram client, the post flow and the delete flow. Gap and per minute cap from the limiter, retry_after honoured,
no double posts, deleteMessages in batches of 100 with the 48 hour tombstone fallback."""
from __future__ import annotations

import html
import logging
import re
import sqlite3
import time

import httpx

log = logging.getLogger(__name__)
TOO_OLD_S = 47 * 3600   # bots cannot delete after 48 h; an hour of margin
REPLACED = "Replaced"
KEEP_KINDS = ("lesson", "profile", "paper", "paper_summary", "move")   # never deleted by a new run


class TelegramError(Exception):
    """Telegram answered ok=false: the request was refused, nothing was sent."""


class Paused(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"Telegram asked to wait {retry_after:.0f} s")
        self.retry_after = retry_after


class Ambiguous(Exception):
    """The request may or may not have been delivered (timeout, 5xx). The caller must not resend."""


class Telegram:
    def __init__(self, token: str, s, limiter=None, transport: httpx.BaseTransport | None = None,
                 clock=time.time, sleep=time.sleep):
        self.s, self.lim = s, limiter
        self.gap, self.max_wait = s.limits.telegram.min_gap_seconds, s.limits.telegram.max_retry_after_seconds
        self.clock, self._sleep, self.last = clock, sleep, 0.0
        self.http = httpx.Client(base_url=f"https://api.telegram.org/bot{token}/", transport=transport, timeout=70)

    def _pace(self, method: str) -> None:
        if method == "getUpdates":
            return
        if self.lim is not None:
            self.lim.acquire("telegram", "bot")
            return
        wait = self.last + self.gap - self.clock()
        if wait > 0:
            self._sleep(wait)
        self.last = self.clock()

    def call(self, method: str, **params):
        throttles = 0
        while True:
            self._pace(method)
            try:
                r = self.http.post(method, json=params)
            except httpx.TransportError as ex:
                raise Ambiguous(f"{method}: {type(ex).__name__}")
            try:
                data = r.json()
            except ValueError:
                data = {}
            if r.status_code == 429:
                retry_after = float(data.get("parameters", {}).get("retry_after", 5))
                throttles += 1
                if throttles > 5 or retry_after > self.max_wait:
                    raise Paused(retry_after)
                log.warning("429 on %s, waiting %.0f s", method, retry_after)
                self._sleep(retry_after + 1)
                continue
            if r.status_code >= 500:
                raise Ambiguous(f"{method}: HTTP {r.status_code}")
            if not data.get("ok"):
                raise TelegramError(f"{method}: {data.get('description', r.status_code)}")
            return data["result"]

    def send(self, chat_id, text: str, reply_to: int | None = None, thread_id: int | None = None,
             buttons: list[tuple[str, str]] | None = None) -> int:
        """HTML message; on 'can't parse entities' it is resent as plain text so it is never lost.
        thread_id posts into a forum topic; buttons is one row of (label, callback_data)."""
        extra = {"reply_parameters": {"message_id": reply_to}} if reply_to else {}
        if thread_id:
            extra["message_thread_id"] = thread_id
        if buttons:
            extra["reply_markup"] = {"inline_keyboard": [[{"text": a, "callback_data": b} for a, b in buttons]]}
        try:
            r = self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                          link_preview_options={"is_disabled": True}, **extra)
        except TelegramError as ex:
            if "parse entities" not in str(ex).lower():
                raise
            r = self.call("sendMessage", chat_id=chat_id, text=html.unescape(re.sub(r"<[^>]+>", "", text)),
                          link_preview_options={"is_disabled": True}, **extra)
        return r["message_id"]

    def post(self, texts: list[str], buttons: list[tuple[str, str]] | None = None) -> list[int]:
        """Send a multi message report to the bot's chat and topic; buttons go on the last message only."""
        chat, thread = self.s.telegram.chat_id, self.s.telegram.get("thread_id")
        return [self.send(chat, t, thread_id=thread, buttons=buttons if i == len(texts) - 1 else None)
                for i, t in enumerate(texts)]

    def poll(self, db, handler, stop=None) -> None:
        """Long poll getUpdates forever; the offset lives in kv so a restart never replays a command."""
        row = db.execute("SELECT value FROM kv WHERE key='tg_offset'").fetchone()
        offset = int(row["value"]) if row else 0
        while not (stop and stop.is_set()):
            try:
                updates = self.call("getUpdates", offset=offset, timeout=50,
                                    allowed_updates=["message", "callback_query"])
            except (Ambiguous, TelegramError, Paused) as ex:
                log.warning("getUpdates: %s", ex)
                self._sleep(10)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES('tg_offset', ?)", (str(offset),))
                try:
                    handler(u)
                except Exception:   # one bad command must never stop the listener
                    log.exception("update %s failed", u.get("update_id"))


def create_posts(db: sqlite3.Connection, run_id: str, chat_id: str, messages: list[dict]) -> int:
    """Store rendered messages as pending, one row per item. UNIQUE(run_id, kind, ref_id) makes this safe to repeat."""
    n = 0
    for m in messages:
        n += db.execute("INSERT OR IGNORE INTO posts(run_id, kind, ref_id, chat_id, body, post_status) "
                        "VALUES(?,?,?,?,?,'pending')", (run_id, m["kind"], str(m["ref_id"]), str(chat_id), m["body"])).rowcount
    return n


def post_pending(db: sqlite3.Connection, tg: Telegram, run_id: str, now=time.time) -> dict[str, int]:
    """Post pending rows in order, once. 'sending' rows (outcome unknown) are never retried."""
    counts = {"posted": 0, "failed": 0}
    for row in db.execute("SELECT * FROM posts WHERE run_id=? AND post_status='pending' ORDER BY id", (run_id,)).fetchall():
        if db.execute("UPDATE posts SET post_status='sending' WHERE id=? AND post_status='pending'", (row["id"],)).rowcount != 1:
            continue
        try:
            mid = tg.send(row["chat_id"], row["body"])
        except TelegramError as ex:
            log.error("post %s %s refused: %s", row["kind"], row["ref_id"], ex)
            db.execute("UPDATE posts SET post_status='failed' WHERE id=?", (row["id"],))
            counts["failed"] += 1
            continue
        except Paused:
            db.execute("UPDATE posts SET post_status='pending' WHERE id=?", (row["id"],))   # never sent, safe later
            raise
        db.execute("UPDATE posts SET post_status='posted', message_id=?, posted_at=? WHERE id=?", (mid, now(), row["id"]))
        counts["posted"] += 1
    return counts


def previous_deal_rows(db: sqlite3.Connection, chat_id, keep_run: str | None) -> list:
    """Deal messages still up from earlier runs, except favourites. Lessons, profiles, moves and paper results stay."""
    return list(db.execute(
        "SELECT * FROM posts WHERE chat_id=? AND kind='deal' AND post_status='posted' AND deleted_at IS NULL "
        "AND (? IS NULL OR run_id<>?) AND message_id NOT IN (SELECT message_id FROM favorites WHERE message_id IS NOT NULL) "
        "ORDER BY id", (str(chat_id), keep_run, keep_run)))


def delete_posts(db: sqlite3.Connection, tg: Telegram, rows: list, now=time.time) -> dict[str, int]:
    """Delete in batches of 100. A message older than 47 h cannot be deleted, so it is edited to 'Replaced'."""
    counts = {"deleted": 0, "replaced": 0, "gone": 0, "failed": 0}
    rows = [dict(r) for r in rows]

    def mark(row, status, key):
        db.execute("UPDATE posts SET deleted_at=?, delete_status=? WHERE id=?", (now(), status, row["id"]))
        counts[key] += 1

    young = []
    for row in rows:
        if (row["posted_at"] or 0) >= now() - TOO_OLD_S:
            young.append(row)
            continue
        try:
            tg.call("editMessageText", chat_id=row["chat_id"], message_id=row["message_id"], text=REPLACED)
            mark(row, "replaced", "replaced")
        except TelegramError as ex:
            if "not modified" in str(ex).lower() or "not found" in str(ex).lower():
                mark(row, "gone", "gone")
            else:
                log.warning("edit failed for %s: %s", row["message_id"], ex)
                counts["failed"] += 1
    for chat in {r["chat_id"] for r in young}:
        mine = [r for r in young if r["chat_id"] == chat]
        for i in range(0, len(mine), 100):
            chunk = mine[i:i + 100]
            try:
                tg.call("deleteMessages", chat_id=chat, message_ids=[r["message_id"] for r in chunk])
                for r in chunk:
                    mark(r, "deleted", "deleted")
                continue
            except TelegramError as ex:
                log.warning("batch delete failed, one at a time: %s", ex)
            for r in chunk:
                try:
                    tg.call("deleteMessage", chat_id=chat, message_id=r["message_id"])
                    mark(r, "deleted", "deleted")
                except TelegramError as ex:
                    if "not found" in str(ex).lower():
                        mark(r, "gone", "gone")
                    else:
                        counts["failed"] += 1
    return counts
