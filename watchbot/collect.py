"""Daily collect: FX, retail, then every enabled source for every watched reference, Singapore filter, upsert,
and the listings that ended. Each change is a listing_events row so the vault can show all movement."""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime

from . import fx, refs as refsmod, region
from .ratelimit import LimitError
from .sources.base import Listing

log = logging.getLogger(__name__)


def upsert(db, l: Listing, today: str) -> tuple[int, str | None]:
    """Insert or refresh one listing. Returns (id, event) with event 'new', 'price' or None."""
    price_sgd = fx.to_sgd(db, l.price, l.currency, today)
    raw = l.as_dict()
    raw.pop("raw", None)
    row = db.execute("SELECT id, price, ended_at FROM listings WHERE source=? AND source_id=?",
                     (l.source, l.source_id)).fetchone()
    if not row:
        cur = db.execute(
            "INSERT INTO listings(source, source_id, ref, title, price, currency, price_sgd, condition, year, full_set, "
            "seller_type, seller_country, feedback_json, url, first_seen, last_seen, raw_json, scheduled_end, missed) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (l.source, l.source_id, l.ref, l.title, l.price, l.currency, price_sgd, l.condition, l.year,
             None if l.full_set is None else int(l.full_set), l.seller_type, l.seller_country,
             json.dumps(l.feedback), l.url, today, today, json.dumps(raw, ensure_ascii=False), l.scheduled_end))
        lid = cur.lastrowid
        db.execute("INSERT INTO listing_events(listing_id, date, kind, new, label) VALUES(?,?,'new',?,'asking')",
                   (lid, today, price_sgd))
        return lid, "new"
    lid, event = row["id"], None
    if l.price is not None and row["price"] is not None and abs(l.price - row["price"]) > 0.5:
        old_sgd = fx.to_sgd(db, row["price"], l.currency, today)
        db.execute("INSERT INTO listing_events(listing_id, date, kind, old, new, label) VALUES(?,?,'price',?,?,'asking')",
                   (lid, today, old_sgd, price_sgd))
        event = "price"
    db.execute("UPDATE listings SET title=?, price=?, currency=?, price_sgd=?, condition=?, year=?, full_set=?, "
               "seller_type=?, seller_country=?, feedback_json=?, url=?, last_seen=?, raw_json=?, scheduled_end=?, "
               "missed=0, ended_at=NULL, end_label=NULL WHERE id=?",
               (l.title, l.price, l.currency, price_sgd, l.condition, l.year,
                None if l.full_set is None else int(l.full_set), l.seller_type, l.seller_country,
                json.dumps(l.feedback), l.url, today, json.dumps(raw, ensure_ascii=False), l.scheduled_end, lid))
    return lid, event


def mark_missing(db, source: str, ref: str, seen_ids: set[int], today: str, now_iso: str, after: int) -> int:
    """Listings of this source and reference not seen in a complete collect. After `after` misses in a row the
    listing has ended: 'likely sold' when it went before its scheduled end or had none, 'expired' otherwise."""
    ended = 0
    rows = db.execute("SELECT id, missed, scheduled_end, last_seen, price_sgd FROM listings WHERE source=? AND ref=? "
                      "AND ended_at IS NULL AND last_seen<?", (source, ref, today)).fetchall()
    for r in rows:
        if r["id"] in seen_ids:
            continue
        missed = (r["missed"] or 0) + 1
        if missed < after:
            db.execute("UPDATE listings SET missed=? WHERE id=?", (missed, r["id"]))
            continue
        label = "expired" if r["scheduled_end"] and r["scheduled_end"] <= now_iso else "likely sold"
        db.execute("UPDATE listings SET missed=?, ended_at=?, end_label=? WHERE id=?",
                   (missed, r["last_seen"], label, r["id"]))
        db.execute("INSERT INTO listing_events(listing_id, date, kind, old, label) VALUES(?,?,'ended',?,?)",
                   (r["id"], today, r["price_sgd"], label))
        ended += 1
    return ended


def run(s, db, lim, *, sources=None, fetcher=None, slow: float = 0.0, transport=None, refs_only=None) -> dict:
    """One collect. Returns per reference counts and notes. `slow` adds seconds between references (backfill)."""
    from .sources.ebay import Ebay, EbayError
    run_id = f"collect-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    db.execute("INSERT INTO runs(id, kind, started_at, status) VALUES(?,?,?,'running')", (run_id, "collect", time.time()))
    refsmod.seed(db, s)
    today = lim.today()
    now_iso = datetime.now().astimezone().isoformat()
    notes, per_ref = [], {}
    try:
        fx.refresh(db, lim, s, transport=transport)
    except LimitError as ex:
        notes.append(f"fx: {ex}")
    if fetcher is not None:
        from .sources import retail
        for ref, what in retail.refresh(db, fetcher, today).items():
            notes.append(f"retail {ref}: {what}")
    srcs = sources if sources is not None else []
    if sources is None and s.sources.ebay.enabled:
        eb = Ebay(db, lim, s, transport=transport)
        if eb.configured:
            srcs.append(eb)
        else:
            notes.append("ebay: EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are not set, skipped")
    for ref in refsmod.watched(db):
        if refs_only and ref["ref"] not in refs_only:
            continue
        c = per_ref.setdefault(ref["ref"], {"found": 0, "kept": 0, "dropped": 0, "new": 0, "price": 0, "ended": 0,
                                            "other_ref": 0, "by_source": {}})
        for src in srcs:
            name = src.source
            try:
                listings, complete, raw_n = src.search(ref)
            except (EbayError, LimitError) as ex:
                notes.append(f"{name} {ref['ref']}: {ex}")
                continue
            kept, dropped = region.keep_sg(listings, s)
            for l, why in dropped:
                log.info("dropped %s %s: %s", l.source, l.source_id, why)
            seen = set()
            for l in kept:
                lid, ev = upsert(db, l, today)
                seen.add(lid)
                if ev:
                    c[ev] += 1
            if complete:
                c["ended"] += mark_missing(db, src.source, ref["ref"], seen, today, now_iso,
                                           s.run.ended_after_missed_runs)
            c["found"] += raw_n
            c["kept"] += len(kept)
            c["dropped"] += len(dropped)
            c["other_ref"] += raw_n - len(listings)
            c["by_source"][name] = len(kept)
            if slow:
                lim.sleep(slow)
    db.execute("UPDATE runs SET finished_at=?, status='ok', note=? WHERE id=?", (time.time(), "; ".join(notes), run_id))
    return {"run_id": run_id, "per_ref": per_ref, "notes": notes}

