"""The reference table: facts and retail for every watched reference, seeded from refdata.yaml."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from . import config

REFDATA = Path(__file__).resolve().parent / "refdata.yaml"


def refdata() -> dict:
    return yaml.safe_load(REFDATA.read_text(encoding="utf-8")) or {}


def seed(db, s) -> int:
    """Insert every watchlist reference once. Never overwrites a retail figure retail.py has refreshed since."""
    data, n = refdata(), 0
    for w in config.watchlist(s):
        d = data.get(w["ref"], {})
        row = db.execute("SELECT retail_date FROM refs WHERE ref=?", (w["ref"],)).fetchone()
        if row:
            continue
        facts = dict(d.get("facts") or {})
        for k in ("retail_confidence", "retail_note"):
            if d.get(k):
                facts[k] = d[k]
        db.execute("INSERT INTO refs(ref, brand, model, facts_json, retail_sgd, retail_url, retail_date, aliases_json, "
                   "watched) VALUES(?,?,?,?,?,?,?,?,1)",
                   (w["ref"], w["brand"] or d.get("brand", ""), w["model"] or d.get("model", ""),
                    json.dumps(facts, ensure_ascii=False), d.get("retail_sgd"), d.get("retail_url"),
                    d.get("retail_date"), json.dumps(d.get("aliases") or [])))
        n += 1
    return n


def get(db, ref: str) -> dict | None:
    r = db.execute("SELECT * FROM refs WHERE ref=?", (ref,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["facts"] = json.loads(d.pop("facts_json") or "{}")
    d["aliases"] = json.loads(d.pop("aliases_json") or "[]")
    return d


def watched(db) -> list[dict]:
    return [get(db, r["ref"]) for r in db.execute("SELECT ref FROM refs WHERE watched=1 ORDER BY rowid")]


def add(db, ref: str, brand: str = "", model: str = "") -> bool:
    d = refdata().get(ref, {})
    cur = db.execute("UPDATE refs SET watched=1 WHERE ref=?", (ref,)).rowcount
    if cur:
        return True
    db.execute("INSERT INTO refs(ref, brand, model, facts_json, retail_sgd, retail_url, retail_date, aliases_json, watched) "
               "VALUES(?,?,?,?,?,?,?,?,1)", (ref, brand or d.get("brand", ""), model or d.get("model", ""),
                                             json.dumps(d.get("facts") or {}), d.get("retail_sgd"), d.get("retail_url"),
                                             d.get("retail_date"), json.dumps(d.get("aliases") or [])))
    return True


def remove(db, ref: str) -> bool:
    return db.execute("UPDATE refs SET watched=0 WHERE ref=?", (ref,)).rowcount > 0
