"""Polite limits in one place. Buckets: web (per domain), ebay, watchcharts, fx, telegram, claude.

Jittered gaps, rolling per minute caps, daily caps that roll over at midnight Asia/Singapore, cooldowns after
429, 403 or three failures in a row. State lives in SQLite so a restart never resets a budget or a cooldown.
"""
from __future__ import annotations

import random
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo


class LimitError(Exception):
    pass


class BudgetExhausted(LimitError):
    pass


class InCooldown(LimitError):
    def __init__(self, bucket: str, key: str, until: float, reason: str):
        super().__init__(f"{bucket} {key} in cooldown until {until:.0f} ({reason})")
        self.bucket, self.key, self.until, self.reason = bucket, key, until, reason


class Limiter:
    def __init__(self, db: sqlite3.Connection, s, clock=time.time, sleep=time.sleep, rng: random.Random | None = None):
        self.db, self.s, self.tz = db, s, ZoneInfo(s.schedule.timezone)
        self.clock, self._sleep, self.rng = clock, sleep, rng or random.Random()
        self.lock = threading.RLock()   # one request at a time across threads (scheduler and chat listener)

    # config views ---------------------------------------------------------------------------------------
    def daily_cap(self, bucket: str) -> float:
        L = self.s.limits
        return {"web": L.web.max_requests_per_day, "chrono24": L.web.chrono24_pages_per_day,
                "ebay": L.daily_calls_ebay, "watchcharts": L.daily_calls_watchcharts, "fx": L.daily_calls_fx,
                "claude": L.claude.max_calls_per_day}.get(bucket, float("inf"))

    def gap(self, bucket: str) -> tuple[float, float]:
        L = self.s.limits
        return {"web": (L.web.per_domain_min_gap_seconds, L.web.per_domain_max_gap_seconds),
                "ebay": (L.ebay_min_gap_seconds, L.ebay_min_gap_seconds * 2),
                "watchcharts": (2, 4), "fx": (2, 4),
                "telegram": (L.telegram.min_gap_seconds, L.telegram.min_gap_seconds)}.get(bucket, (0, 0))

    def today(self) -> str:
        return datetime.fromtimestamp(self.clock(), self.tz).date().isoformat()

    # counters -------------------------------------------------------------------------------------------
    def used(self, name: str) -> int:
        r = self.db.execute("SELECT n FROM counters WHERE name=? AND day=?", (name, self.today())).fetchone()
        return r["n"] if r else 0

    def left(self, name: str) -> float:
        return self.daily_cap(name) - self.used(name)

    def _bump(self, name: str) -> None:
        self.db.execute("INSERT INTO counters(name, day, n) VALUES(?,?,1) ON CONFLICT(name, day) DO UPDATE SET n=n+1",
                        (name, self.today()))

    def _state(self, bucket: str, key: str):
        return self.db.execute("SELECT * FROM rate_state WHERE bucket=? AND key=?", (bucket, key)).fetchone()

    def _upsert(self, bucket: str, key: str, **cols) -> None:
        names = ", ".join(cols)
        self.db.execute(f"INSERT INTO rate_state(bucket, key, {names}) VALUES(?,?,{','.join('?' * len(cols))}) "
                        f"ON CONFLICT(bucket, key) DO UPDATE SET " + ", ".join(f"{c}=excluded.{c}" for c in cols),
                        (bucket, key, *cols.values()))

    # cooldowns ------------------------------------------------------------------------------------------
    def cooldown(self, bucket: str, key: str) -> tuple[float, str] | None:
        r = self._state(bucket, key)
        if r and r["cooldown_until"] and r["cooldown_until"] > self.clock():
            return r["cooldown_until"], r["cooldown_reason"] or ""
        return None

    def trip(self, bucket: str, key: str, reason: str, hours: float | None = None) -> float:
        until = self.clock() + (hours or self.s.limits.web.domain_cooldown_hours) * 3600
        self._upsert(bucket, key, cooldown_until=until, cooldown_reason=reason, fails=0)
        return until

    def failed(self, bucket: str, key: str, reason: str) -> bool:
        """A failure (5xx, timeout). Three in a row cool the key down. Returns True when it tripped."""
        r = self._state(bucket, key)
        fails = (r["fails"] if r else 0) + 1
        if fails >= self.s.limits.web.failures_before_cooldown:
            self.trip(bucket, key, f"{fails} failures in a row, last {reason}")
            return True
        self._upsert(bucket, key, fails=fails)
        return False

    def succeeded(self, bucket: str, key: str) -> None:
        self._upsert(bucket, key, fails=0)

    # acquire --------------------------------------------------------------------------------------------
    def check(self, bucket: str, key: str = "", extra_caps: tuple[str, ...] = ()) -> None:
        if cd := self.cooldown(bucket, key):
            raise InCooldown(bucket, key, *cd)
        for name in (bucket, *extra_caps):
            if self.used(name) >= self.daily_cap(name):
                raise BudgetExhausted(f"{name}: daily budget of {self.daily_cap(name):.0f} reached")

    def acquire(self, bucket: str, key: str = "", url: str = "", extra_caps: tuple[str, ...] = ()) -> None:
        """Block until this key's jittered gap and the bucket's per minute window allow, then count the request."""
        with self.lock:
            self.check(bucket, key, extra_caps)
            r = self._state(bucket, key)
            lo, hi = self.gap(bucket)
            if r and r["last_at"]:
                wait = r["last_at"] + self.rng.uniform(lo, hi) - self.clock()
                if wait > 0:
                    self._sleep(wait)
            if bucket == "telegram":
                self._minute_window(bucket, self.s.limits.telegram.max_per_minute)
            self.check(bucket, key, extra_caps)
            self._upsert(bucket, key, last_at=self.clock())
            self._bump(bucket)
            for name in extra_caps:
                self._bump(name)
            self.db.execute("INSERT INTO requests_log(at, bucket, key, url) VALUES(?,?,?,?)",
                            (self.clock(), bucket, key, url))

    def _minute_window(self, bucket: str, cap: int) -> None:
        while True:
            rows = self.db.execute("SELECT at FROM requests_log WHERE bucket=? AND at>? ORDER BY at",
                                   (bucket, self.clock() - 60)).fetchall()
            if len(rows) < cap:
                return
            self._sleep(rows[0]["at"] + 60 - self.clock() + 0.01)

    def log_status(self, bucket: str, key: str, status: int, note: str = "") -> None:
        self.db.execute("UPDATE requests_log SET status=?, note=? WHERE id=(SELECT MAX(id) FROM requests_log "
                        "WHERE bucket=? AND key=?)", (status, note, bucket, key))

    # claude ---------------------------------------------------------------------------------------------
    def claude_left(self) -> float:
        return self.left("claude")

    def acquire_claude(self) -> None:
        with self.lock:
            if self.claude_left() <= 0:
                raise BudgetExhausted(f"claude: {self.daily_cap('claude')} calls a day already used")
            self._bump("claude")

    def sleep(self, seconds: float) -> None:
        self._sleep(seconds)
