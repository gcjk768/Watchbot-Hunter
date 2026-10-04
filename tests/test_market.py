import json
from datetime import date

from watchbot import cards, market, refs
from watchbot.serve import Bot


def add_listing(db, ref, price, sid, **kw):
    today = date.today().isoformat()
    db.execute("INSERT INTO listings(source, source_id, ref, title, price, currency, price_sgd, first_seen, last_seen, "
               "raw_json, year, full_set, seller_type, feedback_json, url) VALUES('ebay',?,?,?,?,'SGD',?,?,?,?,?,?,?,?,?)",
               (sid, ref, "t", price, price, today, today, json.dumps(kw.get("raw", {})), kw.get("year", 2023),
                kw.get("full_set", 1), kw.get("seller_type", "dealer"), "{}", "https://www.ebay.com.sg/itm/1"))


def test_trimmed_median_drops_outliers():
    assert market.trimmed_median([1, 100, 101, 102, 10_000], 20) == 101


def test_values_label_and_deal_maths(s, db):
    refs.seed(db, s)
    for i, p in enumerate([16000, 16500, 17000, 17500, 18000]):
        add_listing(db, "126610LN", p, f"a{i}")
    add_listing(db, "126610LN", 13000, "cheap")
    mv = market.values(s, db, date.today().isoformat())
    m = mv["126610LN"]
    assert m["label"] == "market" and m["n"] == 6
    # 13,000 is ~20% under a ~16,000 market: below too_good_pct (30), so a deal, not a warning
    ds = market.find_deals(s, db, mv)
    assert [d["listing"]["source_id"] for d in ds] == ["cheap"]
    d = ds[0]
    assert d["landed"] == sum(d["costs"].values()) and d["net"] == d["exit"] - d["landed"]
    assert not any(f.startswith("too good") for f in d["flags"])
    # alerted once only
    assert len(market.new_deals(db, ds, "r1", "2026-10-03")) == 1
    assert market.new_deals(db, ds, "r2", "2026-10-03") == []


def test_thin_market_never_makes_deals(s, db):
    refs.seed(db, s)
    add_listing(db, "126610LN", 16000, "a")
    add_listing(db, "126610LN", 9000, "b")
    mv = market.values(s, db, date.today().isoformat())
    assert mv["126610LN"]["label"] == "thin" and market.find_deals(s, db, mv) == []


def test_cards_escape_and_split():
    ref = {"brand": "A<b>", "model": "M&M", "ref": "X1", "retail_sgd": None, "retail_date": None}
    out = cards.market([ref] * 60, {}, "2026-10-03")
    assert len(out) > 1 and all(len(t) <= 4096 for t in out)
    assert "A&lt;b&gt;" in out[0] and "<b>A<b>" not in out[0]


class FakeTg:
    def __init__(self):
        self.calls = []

    def call(self, method, **kw):
        self.calls.append(method)

    def send(self, chat, text, **kw):
        self.calls.append(("send", chat, kw.get("thread_id")))


class X:
    def __init__(self, s, db, lim):
        self.s, self.db, self.lim, self.tg = s, db, lim, FakeTg()


def test_bot_owner_and_topic_only(s, db, lim):
    s.telegram.owner_user_id, s.telegram.chat_id, s.telegram.thread_id = <THREAD_ID>, "-1001", 55
    bot = Bot(X(s, db, lim))
    group = {"id": -1001, "type": "supergroup"}
    assert bot.allowed({"from": {"id": 7}, "chat": {"type": "private"}})
    assert bot.allowed({"from": {"id": 7}, "chat": group, "message_thread_id": <THREAD_ID>})
    assert not bot.allowed({"from": {"id": 7}, "chat": group, "message_thread_id": <THREAD_ID>})   # another bot's topic
    assert not bot.allowed({"from": {"id": 8}, "chat": {"type": "private"}})


def test_callback_whitelist_always_answers(s, db, lim):
    s.telegram.owner_user_id = 7
    x = X(s, db, lim)
    Bot(x).handle({"callback_query": {"id": "q", "data": "watchadd 1 2", "from": {"id": 7},
                                      "message": {"chat": {"id": 7, "type": "private"}}}})
    assert x.tg.calls == ["answerCallbackQuery"]   # not whitelisted: answered, nothing run


def test_listing_card_marks_below_retail_green_and_escapes():
    ref = {"brand": "Rolex", "model": "Sub <x>", "ref": "126610LN", "retail_sgd": 15950}
    l = {"price_sgd": 14000, "source": "discover", "site": "watchexchange.sg", "url": "https://a.sg/?a=1&b=2",
         "raw_json": '{"from_search": true}', "condition": "excellent", "year": 2022, "full_set": 1, "seller_type": "dealer"}
    out = cards.listings([(l, ref, None)])
    assert len(out) == 1 and "🟢 <i>▼1,950 (12.2%) vs retail</i>" in out[0] and "Sub &lt;x&gt;" in out[0]
    # same card head as the SG car tracker: numbered, the name is the link, the reference after a dot
    assert '⌚ <b>1. <a href="https://a.sg/?a=1&amp;b=2">Rolex Sub &lt;x&gt;</a></b> · 126610LN' in out[0]
    assert out[0].startswith("⌚ <b>WATCH LISTINGS</b> · 1 in Singapore") and out[0].endswith("</blockquote>")
    assert "💰 $14,000 · 2022 · full set · excellent" in out[0]
    assert 'href="https://a.sg/?a=1&amp;b=2"' in out[0] and "<i>from search</i>" in out[0]
    assert "🛒 <a" in out[0] and "Buy at a.sg</a>" in out[0] and "🏠 a.sg · dealer" in out[0]


def test_new_finds_alert_once_and_skip_deals(s, db, lim):
    from watchbot.serve import new_finds
    refs.seed(db, s)
    add_listing(db, "126610LN", 16000, "a")
    add_listing(db, "126300", 12000, "b")
    db.execute("INSERT INTO deals(listing_id) SELECT id FROM listings WHERE source_id='b'")
    out = new_finds(X(s, db, lim))
    assert len(out) == 1 and "🆕" in out[0] and "126610LN" in out[0] and "126300" not in out[0]
    assert new_finds(X(s, db, lim)) == []


def test_discovered_links_must_be_product_pages_for_the_ref():
    from watchbot.ai import buyable
    sub = {"ref": "126610LN", "aliases": []}
    assert buyable("https://watchexchange.sg/watches/rolex/submariner/126610ln/", sub)
    assert not buyable("https://watchexchange.sg/watches/omega/speedmaster/", {"ref": "310.30.42.50.01.001"})
    assert not buyable("https://www.thehourglass.com/en-VN/product/longines/l3-812-4-53-6", {"ref": "L3.802.4.63.6"})
    assert not buyable("https://www.thehourglass.com/en-MY/product/iwc/iw371605", {"ref": "IW371605"})
    assert buyable("https://www.thehourglass.com/en-sg/product/iwc/iw371605", {"ref": "IW371605"})
    assert buyable("https://kimwatch.sg/products/santos-de-cartier-medium-wssa0029-unworn", {"ref": "WSSA0029"})


def test_focus_cards_cheapest_and_trend(s, db):
    refs.seed(db, s)
    for i, p in enumerate([16000, 16500, 17000, 17500, 18000]):
        add_listing(db, "126610LN", p, f"a{i}")
    add_listing(db, "124060", 12000, "cheap")
    db.execute("INSERT INTO market_daily(ref,date,condition,set_type,value_sgd,label,n_comparables) "
               "VALUES('126610LN','2026-08-01','any','any',15000,'market',6)")
    today = date.today().isoformat()
    mv = market.values(s, db, today)
    out = market.focus(s, db, mv, today)
    assert [f["title"] for f in out] == ["Omega Speedmaster", "Rolex Submariner", "Rolex popular and appreciating"]
    pop = out[2]
    assert len(pop["refs"]) == 14 and len(cards.focus_card(pop)) <= 4096
    sub = out[1]
    assert sub["cheapest"]["ref"] == "124060" and out[0]["cheapest"] is None
    t = next(r for r in sub["refs"] if r["ref"]["ref"] == "126610LN")["trend"]
    assert t["since"] == "2026-08-01" and t["pct"] > 0
    texts = [cards.focus_card(f) for f in out[:2]]
    assert "APPRECIATING" in texts[1] and "Cheapest now" in texts[0] and all(len(t) <= 4096 for t in texts)
