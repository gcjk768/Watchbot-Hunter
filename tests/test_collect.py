from datetime import date, timedelta

import httpx

from watchbot import collect

FX = httpx.MockTransport(lambda r: httpx.Response(200, json={"rates": {"USD": 0.78}}))
from watchbot.sources.base import Listing


class FakeSource:
    source = "ebay"

    def __init__(self):
        self.items = {}

    def search(self, ref):
        ls = self.items.get(ref["ref"], [])
        return ls, True, len(ls)


def L(i, price, end=None, country="SG"):
    return Listing(source="ebay", source_id=str(i), ref="126610LN", title=f"Rolex 126610LN {i}", price=price,
                   currency="SGD", url=f"https://www.ebay.com.sg/itm/{i}", seller_country=country, item_country=country,
                   scheduled_end=end)


def day(lim, clock, n):
    clock.t += 86400 * n


def test_new_price_change_and_likely_sold(s, db, lim, clock):
    src = FakeSource()
    src.items["126610LN"] = [L(1, 15000), L(2, 14000), L(3, 9000, country="MY")]
    out = collect.run(s, db, lim, sources=[src], refs_only=["126610LN"], transport=FX)
    c = out["per_ref"]["126610LN"]
    assert c["new"] == 2 and c["dropped"] == 1
    day(lim, clock, 1)
    src.items["126610LN"] = [L(1, 14500)]
    c = collect.run(s, db, lim, sources=[src], refs_only=["126610LN"], transport=FX)["per_ref"]["126610LN"]
    assert c["price"] == 1 and c["ended"] == 0          # one miss is not yet an end
    day(lim, clock, 1)
    c = collect.run(s, db, lim, sources=[src], refs_only=["126610LN"], transport=FX)["per_ref"]["126610LN"]
    assert c["ended"] == 1
    r = db.execute("SELECT end_label, ended_at FROM listings WHERE source_id='2'").fetchone()
    assert r["end_label"] == "likely sold"
    kinds = [r["kind"] for r in db.execute("SELECT kind FROM listing_events ORDER BY id")]
    assert kinds == ["new", "new", "price", "ended"]


def test_listing_past_its_scheduled_end_is_expired_not_sold(s, db, lim, clock):
    src = FakeSource()
    src.items["126610LN"] = [L(5, 15000, end="2020-01-01T00:00:00+00:00")]
    collect.run(s, db, lim, sources=[src], refs_only=["126610LN"], transport=FX)
    src.items["126610LN"] = []
    for _ in range(2):
        day(lim, clock, 1)
        collect.run(s, db, lim, sources=[src], refs_only=["126610LN"], transport=FX)
    assert db.execute("SELECT end_label FROM listings").fetchone()[0] == "expired"


def test_incomplete_search_never_ends_listings(s, db, lim, clock):
    class Partial(FakeSource):
        def search(self, ref):
            return [], False, 0
    src = FakeSource()
    src.items["126610LN"] = [L(1, 15000)]
    collect.run(s, db, lim, sources=[src], refs_only=["126610LN"], transport=FX)
    for _ in range(3):
        day(lim, clock, 1)
        collect.run(s, db, lim, sources=[Partial()], refs_only=["126610LN"], transport=FX)
    assert db.execute("SELECT ended_at FROM listings").fetchone()[0] is None


def test_seed_loads_starter_watchlist_with_retail(s, db):
    from watchbot import refs
    assert refs.seed(db, s) == len(s.starter_watchlist) >= 66
    sub = refs.get(db, "126610LN")
    assert sub["retail_sgd"] == 15950 and sub["retail_url"].startswith("https://www.rolex.com/en-sg/")
    assert refs.get(db, "124300")["retail_sgd"] is None    # discontinued, never guessed
    assert refs.seed(db, s) == 0
