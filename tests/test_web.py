import httpx
import pytest

from watchbot.web import Blocked, Fetcher

ROBOTS = "User-agent: *\nDisallow: /search/\nAllow: /p/\n"


def fetcher(db, lim, s, handler):
    return Fetcher(db, lim, s, transport=httpx.MockTransport(handler))


def site(calls, robots=ROBOTS, page="<html>ok</html>", page_status=200, headers=None):
    def h(req):
        calls.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(page_status, text=page, headers=headers or {})
    return h


@pytest.mark.parametrize("url", ["https://www.carousell.sg/p/rolex-123", "https://facebook.com/marketplace/item/1",
                                 "https://www.instagram.com/p/x", "https://old.reddit.com/r/watchexchange",
                                 "https://www.xiaohongshu.com/explore/1"])
def test_never_fetch_refused_before_any_request(db, lim, s, url):
    calls = []
    f = fetcher(db, lim, s, site(calls))
    with pytest.raises(Blocked):
        f.get(url)
    assert calls == [] and lim.used("web") == 0


def test_terms_gate_chrono24_and_unverified_dealers(db, lim, s):
    calls = []
    f = fetcher(db, lim, s, site(calls))
    for url in ("https://www.chrono24.sg/search/index.htm?query=126610LN", "https://watchexchange.sg/products/x",
                "https://watchcharts.com/watch_model/1"):
        with pytest.raises(Blocked):
            f.get(url)
    assert calls == []
    s.rules["sg_dealers"]["watchexchange.sg"]["terms"] = "allowed"
    assert f.get("https://watchexchange.sg/products/x").status == 200


def test_robots_disallow_is_obeyed(db, lim, s):
    calls = []
    f = fetcher(db, lim, s, site(calls))
    with pytest.raises(Blocked):
        f.get("https://shop.example.sg/search/?q=rolex")
    assert calls == ["https://shop.example.sg/robots.txt"]


def test_robots_fetched_once_a_day(db, lim, s, clock):
    calls = []
    f = fetcher(db, lim, s, site(calls))
    f.get("https://shop.example.sg/p/1")
    f.get("https://shop.example.sg/p/2")
    assert sum(c.endswith("robots.txt") for c in calls) == 1
    clock.t += 25 * 3600
    f.get("https://shop.example.sg/p/3")
    assert sum(c.endswith("robots.txt") for c in calls) == 2


def test_unreachable_robots_disallows(db, lim, s):
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(503)
        return httpx.Response(200, text="x")
    with pytest.raises(Blocked):
        fetcher(db, lim, s, h).get("https://shop.example.sg/p/1")


def test_page_cache_and_conditional_get(db, lim, s, clock):
    calls = []
    f = fetcher(db, lim, s, site(calls, headers={"ETag": '"v1"'}))
    assert f.get("https://shop.example.sg/p/1").text == "<html>ok</html>"
    n = len(calls)
    assert f.get("https://shop.example.sg/p/1").from_cache and len(calls) == n   # within cache hours: no request
    clock.t += 21 * 3600
    p = f.get("https://shop.example.sg/p/1")
    assert p.from_cache and p.text == "<html>ok</html>" and len(calls) == n + 1   # revalidated, 304


def test_403_cools_the_domain(db, lim, s):
    from watchbot.ratelimit import InCooldown
    calls = []
    f = fetcher(db, lim, s, site(calls, page_status=403))
    with pytest.raises(InCooldown):
        f.get("https://shop.example.sg/p/1")
    with pytest.raises(InCooldown):
        f.get("https://shop.example.sg/p/2")


def test_identifies_the_bot(db, lim, s):
    seen = []

    def h(req):
        seen.append(req.headers["user-agent"])
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, text="x")
    fetcher(db, lim, s, h).get("https://shop.example.sg/p/1")
    assert set(seen) == {"watchbot/1.0 (personal research, low volume)"}
