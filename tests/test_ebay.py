import json

import httpx

from conftest import fixture_json
from watchbot import region
from watchbot.sources.ebay import Ebay, parse_search

REF = {"ref": "126610LN", "brand": "Rolex", "aliases": ["126610 LN"]}


def test_parse_fixture_keeps_only_this_reference_and_whole_watches(s):
    n, ls = parse_search(fixture_json("ebay_search_126610LN.json"), REF)
    assert n == 8
    ids = {l.source_id.split("|")[1][-1] for l in ls}
    assert "3" not in ids          # 126610LV is another watch
    assert "6" not in ids          # for parts
    first = next(l for l in ls if l.source_id.endswith("1|0"))
    assert first.price == 15800 and first.currency == "SGD" and first.full_set is True and first.year == 2022
    assert first.condition == "good" and first.feedback == {"score": 512, "pct": 99.6}
    assert first.shipping == 0 and first.url.startswith("https://www.ebay.com.sg/itm/") and len(first.images) == 2
    unworn = next(l for l in ls if "Unworn" in l.title)
    assert unworn.condition == "new" and unworn.seller_type == "dealer"
    bp = next(l for l in ls if "B&P" in l.title)
    assert bp.full_set is True and bp.condition == "excellent" and bp.scheduled_end and bp.buying_options == ["AUCTION"]
    assert next(l for l in ls if "watch only" in l.title).full_set is False


def test_singapore_filter_drops_every_non_sg_ebay_result(s):
    _, ls = parse_search(fixture_json("ebay_search_126610LN.json"), REF)
    kept, dropped = region.keep_sg(ls, s)
    assert {l.item_country for l in kept} == {"SG"}
    assert {l.item_country for l, _ in dropped} == {"MY", "US"}


def test_client_credentials_and_sg_search(db, lim, s):
    reqs = []

    def h(req):
        reqs.append(req)
        if req.url.path.endswith("/token"):
            assert req.headers["authorization"].startswith("Basic ")
            assert b"scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope" in req.content
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        return httpx.Response(200, json=fixture_json("ebay_search_126610LN.json"))
    eb = Ebay(db, lim, s, transport=httpx.MockTransport(h))
    ls, complete, n = eb.search(REF)
    assert complete and n == 8 and ls
    search = reqs[-1]
    assert search.headers["x-ebay-c-marketplace-id"] == "EBAY_SG"
    assert search.url.params["filter"] == "itemLocationCountry:SG"
    assert search.headers["authorization"] == "Bearer tok"
    eb.search(REF)
    assert sum(r.url.path.endswith("/token") for r in reqs) == 1   # token reused
    assert lim.used("ebay") == 3


def test_daily_ebay_cap_stops_search(db, lim, s):
    s.limits.daily_calls_ebay = 1
    eb = Ebay(db, lim, s, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"access_token": "t", "expires_in": 7200})))
    ls, complete, _ = eb.search(REF)
    assert ls == [] and complete is False
