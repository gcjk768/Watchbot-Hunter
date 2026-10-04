"""SQLite schema and connection. WAL mode, autocommit, rows as dicts."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
-- rate limits: per bucket and key (domain), daily counters, every request logged
CREATE TABLE IF NOT EXISTS rate_state(
  bucket TEXT, key TEXT, last_at REAL, cooldown_until REAL, cooldown_reason TEXT, fails INTEGER DEFAULT 0,
  PRIMARY KEY(bucket, key));
CREATE TABLE IF NOT EXISTS counters(
  name TEXT, day TEXT, n INTEGER DEFAULT 0, PRIMARY KEY(name, day));
CREATE TABLE IF NOT EXISTS requests_log(
  id INTEGER PRIMARY KEY, at REAL, bucket TEXT, key TEXT, url TEXT, status INTEGER, note TEXT);
CREATE INDEX IF NOT EXISTS requests_log_at ON requests_log(bucket, at);
CREATE TABLE IF NOT EXISTS robots(
  domain TEXT PRIMARY KEY, fetched_at REAL, body TEXT, status INTEGER);
-- page cache with validators for conditional GET
CREATE TABLE IF NOT EXISTS pages(
  url TEXT PRIMARY KEY, fetched_at REAL, status INTEGER, etag TEXT, last_modified TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS kv(
  key TEXT PRIMARY KEY, value TEXT, expires_at REAL);

CREATE TABLE IF NOT EXISTS refs(
  ref TEXT PRIMARY KEY, brand TEXT, model TEXT, facts_json TEXT, retail_sgd REAL, retail_url TEXT, retail_date TEXT,
  aliases_json TEXT DEFAULT '[]', watched INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS listings(
  id INTEGER PRIMARY KEY, source TEXT, source_id TEXT, ref TEXT, title TEXT, price REAL, currency TEXT,
  price_sgd REAL, condition TEXT, year INTEGER, full_set INTEGER, seller_type TEXT, seller_country TEXT,
  feedback_json TEXT, url TEXT, first_seen TEXT, last_seen TEXT, ended_at TEXT, end_label TEXT, raw_json TEXT,
  missed INTEGER DEFAULT 0, scheduled_end TEXT, UNIQUE(source, source_id));
CREATE INDEX IF NOT EXISTS listings_ref ON listings(ref, last_seen);
-- every listing change: new, price, ended. The vault's daily note reads this
CREATE TABLE IF NOT EXISTS listing_events(
  id INTEGER PRIMARY KEY, listing_id INTEGER, date TEXT, kind TEXT, old REAL, new REAL, label TEXT);
CREATE TABLE IF NOT EXISTS market_daily(
  ref TEXT, date TEXT, condition TEXT, set_type TEXT, value_sgd REAL, label TEXT, n_comparables INTEGER,
  liquidity_days REAL, listings_per_week REAL, PRIMARY KEY(ref, date, condition, set_type));
CREATE TABLE IF NOT EXISTS fx(
  date TEXT, currency TEXT, rate REAL, PRIMARY KEY(date, currency));   -- SGD for one unit of currency
CREATE TABLE IF NOT EXISTS news(
  id INTEGER PRIMARY KEY, ref_or_brand TEXT, headline TEXT, gist TEXT, url TEXT, date TEXT, found_at TEXT,
  UNIQUE(url));
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, kind TEXT, started_at REAL, finished_at REAL, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS deals(
  id INTEGER PRIMARY KEY, listing_id INTEGER, run_id TEXT, costs_json TEXT, score REAL, flags_json TEXT, label TEXT,
  status TEXT DEFAULT 'open', created_at TEXT);
CREATE TABLE IF NOT EXISTS paper_trades(
  id INTEGER PRIMARY KEY, deal_id INTEGER, ref TEXT, condition TEXT, set_type TEXT, opened_at TEXT, landed_sgd REAL,
  target_sale_sgd REAL, closed_at TEXT, close_value_sgd REAL, close_reason TEXT, net_sgd REAL, margin_pct REAL,
  marks_json TEXT DEFAULT '[]', mode TEXT CHECK(mode IN ('paper','real')));
CREATE TABLE IF NOT EXISTS lessons(
  id INTEGER PRIMARY KEY, topic TEXT, cycle INTEGER, title TEXT, body_json TEXT, posted_at TEXT);
-- one row per Telegram message. 'sending' is written before the API call, so a crash never double posts
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY, run_id TEXT, kind TEXT, ref_id TEXT, chat_id TEXT, body TEXT, message_id INTEGER,
  post_status TEXT CHECK(post_status IN ('pending','sending','posted','failed')), posted_at REAL, deleted_at REAL,
  delete_status TEXT, UNIQUE(run_id, kind, ref_id));
-- listings already sent as a new finding card, so each one alerts once
CREATE TABLE IF NOT EXISTS alerted(
  listing_id INTEGER PRIMARY KEY, at TEXT);
CREATE TABLE IF NOT EXISTS favorites(
  id INTEGER PRIMARY KEY, message_id INTEGER, deal_id INTEGER, saved_at TEXT);
CREATE TABLE IF NOT EXISTS rules(
  key TEXT PRIMARY KEY, value_json TEXT, url TEXT, checked_at TEXT);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn
