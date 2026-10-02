"""Load config.yaml, rules.yaml and .env, and refuse limits looser than the hard rules."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_NEVER_FETCH = {"carousell.sg", "facebook.com", "instagram.com", "reddit.com", "xiaohongshu.com"}


class ConfigError(Exception):
    pass


class Cfg(SimpleNamespace):
    """Dot access over the yaml dicts. Keys with spaces (Grand Seiko) work through cfg["Grand Seiko"] or .get()."""

    def __getitem__(self, k):
        return getattr(self, k)

    def __contains__(self, k):
        return k in self.__dict__

    def get(self, k, default=None):
        return self.__dict__.get(k, default)

    def items(self):
        return self.__dict__.items()

    def keys(self):
        return self.__dict__.keys()


def wrap(d):
    if isinstance(d, dict):
        c = Cfg()
        for k, v in d.items():
            setattr(c, str(k), wrap(v))
        return c
    if isinstance(d, list):
        return [wrap(v) for v in d]
    return d


def unwrap(c):
    if isinstance(c, Cfg):
        return {k: unwrap(v) for k, v in c.items()}
    if isinstance(c, list):
        return [unwrap(v) for v in c]
    return c


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def check_floors(s) -> None:
    """The limits in the spec. Raising here stops the bot before it does anything."""
    w, r = s.limits.web, s.run
    rules = [
        ("limits.web.per_domain_min_gap_seconds", w.per_domain_min_gap_seconds < 5, "must be at least 5"),
        ("limits.web.per_domain_max_gap_seconds", w.per_domain_max_gap_seconds < w.per_domain_min_gap_seconds,
         "must be at least per_domain_min_gap_seconds"),
        ("limits.web.max_requests_per_day", w.max_requests_per_day > 300, "must be at most 300"),
        ("limits.web.respect_robots_txt", w.respect_robots_txt is not True, "must be true"),
        ("limits.web.chrono24_pages_per_day", w.chrono24_pages_per_day > 50, "must be at most 50"),
        ("limits.web.domain_cooldown_hours", w.domain_cooldown_hours < 24, "must be at least 24"),
        ("limits.telegram.max_per_minute", s.limits.telegram.max_per_minute > 20, "must be at most 20"),
        ("limits.telegram.min_gap_seconds", s.limits.telegram.min_gap_seconds < 3, "must be at least 3"),
        ("limits.claude.max_calls_per_day", s.limits.claude.max_calls_per_day > 12, "must be at most 12"),
        ("run.too_good_pct", r.too_good_pct < 15, "must be at least 15"),
        ("never_fetch_domains", not REQUIRED_NEVER_FETCH <= set(s.never_fetch_domains),
         f"must keep {', '.join(sorted(REQUIRED_NEVER_FETCH))}"),
        ("region.country", s.region.country != "SG", "must be SG"),
        ("sources.ebay.item_location", s.sources.ebay.item_location != "SG", "must be SG"),
        ("sources.chrono24.seller_country", s.sources.chrono24.seller_country != "SG", "must be SG"),
    ]
    for key, bad, why in rules:
        if bad:
            raise ConfigError(f"Refusing to start: {key} {why}.")


def check_paper_lock(s, db) -> None:
    """Rule 5: real buy signals only after paper_min_trades closed paper trades, unless paper_override is set by hand."""
    if s.me.paper_mode or s.me.get("paper_override"):
        return
    closed = closed_paper_trades(db)
    if closed < s.me.paper_min_trades:
        raise ConfigError(f"Refusing to start: me.paper_mode is false but only {closed} paper trades have closed "
                          f"(paper_min_trades {s.me.paper_min_trades}). Set paper_override: true to force it.")


def closed_paper_trades(db) -> int:
    return db.execute("SELECT COUNT(*) FROM paper_trades WHERE mode='paper' AND closed_at IS NOT NULL").fetchone()[0]


def load(path: str | Path | None = None, env: bool = True) -> Cfg:
    path = Path(path) if path else ROOT / "config.yaml"
    if env:
        load_env(path.parent / ".env")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules_path = path.parent / "rules.yaml"
    for env_key, key in (("TELEGRAM_CHAT_ID", "chat_id"), ("TELEGRAM_ADMIN_CHAT_ID", "admin_chat_id")):
        if os.environ.get(env_key):
            raw["telegram"][key] = os.environ[env_key]
    if os.environ.get("TELEGRAM_OWNER_USER_ID"):
        raw["telegram"]["owner_user_id"] = int(os.environ["TELEGRAM_OWNER_USER_ID"])
    s = wrap(raw)
    s.rules = yaml.safe_load(rules_path.read_text(encoding="utf-8")) if rules_path.exists() else {}
    s.root = path.parent.resolve()
    s.data_dir = Path(os.environ.get("WATCHBOT_DATA_DIR") or s.root / "data")
    s.db_path = s.data_dir / "watchbot.db"
    s.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    s.secrets = Cfg(ebay_client_id=os.environ.get("EBAY_CLIENT_ID", ""),
                    ebay_client_secret=os.environ.get("EBAY_CLIENT_SECRET", ""),
                    watchcharts_api_key=os.environ.get("WATCHCHARTS_API_KEY", ""))
    if os.environ.get("VAULT_DIR"):
        s.vault.path = os.environ["VAULT_DIR"]
    check_floors(s)
    return s


def watchlist(s, db=None) -> list[dict]:
    """The references the bot follows: refs added with /watch, else me.watchlist_refs, else the starter list."""
    starter = {w.ref: {"brand": w.brand, "model": w.model, "ref": w.ref} for w in s.starter_watchlist}
    if db is not None:
        rows = db.execute("SELECT ref, brand, model FROM refs WHERE watched=1 ORDER BY rowid").fetchall()
        if rows:
            return [dict(r) for r in rows]
    if s.me.watchlist_refs:
        return [starter.get(r, {"brand": "", "model": "", "ref": r}) for r in s.me.watchlist_refs]
    return list(starter.values())
