import pytest

from watchbot import config


@pytest.mark.parametrize("path,value", [
    ("limits.web.per_domain_min_gap_seconds", 4),
    ("limits.web.max_requests_per_day", 301),
    ("limits.web.respect_robots_txt", False),
    ("limits.web.chrono24_pages_per_day", 51),
    ("limits.telegram.max_per_minute", 21),
    ("limits.claude.max_calls_per_day", 13),
    ("run.too_good_pct", 14),
    ("region.country", "MY"),
    ("sources.ebay.item_location", "US"),
])
def test_floors_refuse_looser_limits(s, path, value):
    obj = s
    *parts, last = path.split(".")
    for p in parts:
        obj = obj[p]
    setattr(obj, last, value)
    with pytest.raises(config.ConfigError):
        config.check_floors(s)


def test_floors_refuse_dropping_a_never_fetch_domain(s):
    s.never_fetch_domains = [d for d in s.never_fetch_domains if d != "reddit.com"]
    with pytest.raises(config.ConfigError):
        config.check_floors(s)


def test_defaults_pass(s):
    config.check_floors(s)


def close_paper(db, n):
    for i in range(n):
        db.execute("INSERT INTO paper_trades(deal_id, opened_at, landed_sgd, closed_at, mode) VALUES(?,?,?,?,'paper')",
                   (i, "2026-01-01", 1000, "2026-02-01"))


def test_paper_lock_blocks_real_mode_before_min_trades(s, db):
    s.me.paper_mode = False
    close_paper(db, 9)
    with pytest.raises(config.ConfigError):
        config.check_paper_lock(s, db)
    close_paper(db, 1)
    config.check_paper_lock(s, db)


def test_paper_override_set_by_owner_unlocks(s, db):
    s.me.paper_mode, s.me.paper_override = False, True
    config.check_paper_lock(s, db)


def test_open_or_real_trades_do_not_count(s, db):
    s.me.paper_mode = False
    for i in range(10):
        db.execute("INSERT INTO paper_trades(deal_id, opened_at, landed_sgd, mode) VALUES(?,?,?,'paper')", (i, "x", 1))
        db.execute("INSERT INTO paper_trades(deal_id, opened_at, landed_sgd, closed_at, mode) VALUES(?,?,?,?,'real')",
                   (i, "x", 1, "y"))
    with pytest.raises(config.ConfigError):
        config.check_paper_lock(s, db)
