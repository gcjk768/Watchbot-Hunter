"""The only way the bot reads web pages. One request at a time, own user agent, robots.txt first, never_fetch first of all.

APIs (eBay, WatchCharts, FX) do not come through here; they use their own buckets in ratelimit.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .ratelimit import InCooldown, Limiter

log = logging.getLogger(__name__)
ROBOTS_TTL = 24 * 3600
# sources whose terms forbid automated access, or have not been confirmed, by host suffix
TERMS_HOSTS = {"carousell": ("carousell.sg", "carousell.com"), "chrono24": ("chrono24.com", "chrono24.sg"),
               "watchcharts_site": ("watchcharts.com",)}


class Blocked(Exception):
    """Never fetched: never_fetch domain, forbidden or unverified terms, a robots.txt disallow, or a non http URL."""


@dataclass
class Page:
    url: str
    status: int
    text: str
    from_cache: bool = False


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_never_fetch(url: str, s) -> bool:
    return matches(host_of(url), s.never_fetch_domains)


def terms_block(url: str, s) -> str | None:
    """Why this host may not be fetched under its terms, or None. Dealers need terms: allowed in rules.yaml."""
    host, rules = host_of(url), s.rules or {}
    for source, hosts in TERMS_HOSTS.items():
        if matches(host, hosts):
            verdict = rules.get("sources", {}).get(source, {}).get("verdict", "unverified")
            return None if verdict == "allowed" else f"{source} terms: {verdict}"
    for domain, d in (rules.get("sg_dealers") or {}).items():
        if matches(host, [domain]) and d.get("terms") != "allowed":
            return f"{domain} terms: {d.get('terms', 'unverified')}"
    return None


class Robots:
    """robots.txt per host, fetched at most once a day. 404 allows; unreachable, 401, 403 or 5xx disallow."""

    def __init__(self, f: "Fetcher"):
        self.f = f

    def allowed(self, url: str) -> bool:
        u = urlparse(url)
        host = u.netloc.lower()
        row = self.f.db.execute("SELECT * FROM robots WHERE domain=?", (host,)).fetchone()
        if not row or row["fetched_at"] < self.f.lim.clock() - ROBOTS_TTL:
            body, status = self._download(f"{u.scheme}://{host}/robots.txt")
            self.f.db.execute("INSERT OR REPLACE INTO robots(domain, fetched_at, body, status) VALUES(?,?,?,?)",
                              (host, self.f.lim.clock(), body, status))
        else:
            body, status = row["body"], row["status"]
        return self.verdict(body, status, url, self.f.ua)

    @staticmethod
    def verdict(body: str, status: int, url: str, ua: str) -> bool:
        if status == 404 or (400 <= status < 500 and status not in (401, 403, 429)):
            return True
        if status != 200:
            return False
        rp = RobotFileParser()
        rp.parse(body.splitlines())
        return rp.can_fetch(ua, url)

    def crawl_delay(self, host: str) -> float | None:
        row = self.f.db.execute("SELECT body FROM robots WHERE domain=?", (host,)).fetchone()
        if not row or not row["body"]:
            return None
        rp = RobotFileParser()
        rp.parse(row["body"].splitlines())
        d = rp.crawl_delay(self.f.ua)
        return float(d) if d else None

    def _download(self, robots_url: str) -> tuple[str, int]:
        for _ in range(3):
            if is_never_fetch(robots_url, self.f.s):
                return "", 0
            host = host_of(robots_url)
            self.f.lim.acquire("web", host, robots_url)
            try:
                r = self.f.http.get(robots_url)
            except httpx.HTTPError as ex:
                log.warning("robots.txt unreachable for %s: %s", host, type(ex).__name__)
                self.f.lim.failed("web", host, type(ex).__name__)
                return "", 0
            self.f.lim.log_status("web", host, r.status_code, "robots")
            if r.status_code in (301, 302, 307, 308) and r.headers.get("location"):
                robots_url = urljoin(robots_url, r.headers["location"])
                continue
            if r.status_code in (403, 429):
                self.f.lim.trip("web", host, f"HTTP {r.status_code} on robots.txt")
            return (r.text if r.status_code == 200 else ""), r.status_code
        return "", 0


class Fetcher:
    def __init__(self, db, limiter: Limiter, s, transport: httpx.BaseTransport | None = None):
        self.db, self.lim, self.s = db, limiter, s
        self.ua = s.limits.web.user_agent
        self.http = httpx.Client(headers={"User-Agent": self.ua, "Accept-Language": "en-SG,en;q=0.9"},
                                 timeout=s.limits.web.timeout_seconds, follow_redirects=False, transport=transport)
        self.robots = Robots(self)

    def refuse(self, url: str) -> None:
        """Every reason not to touch a URL, checked before any request, robots.txt included."""
        if urlparse(url).scheme not in ("http", "https"):
            raise Blocked(f"not an http url: {url}")
        if is_never_fetch(url, self.s):
            raise Blocked(f"never_fetch domain: {host_of(url)}")
        if why := terms_block(url, self.s):
            raise Blocked(why)

    def get(self, url: str, extra_caps: tuple[str, ...] = (), hops: int = 3) -> Page:
        """Raises Blocked, InCooldown, BudgetExhausted. Returns the page for any other status.
        A page fetched within page_cache_hours costs no request; an older one is revalidated with a conditional GET."""
        self.refuse(url)
        row = self.db.execute("SELECT * FROM pages WHERE url=?", (url,)).fetchone()
        if row and row["status"] == 200 and row["fetched_at"] > self.lim.clock() - self.s.limits.web.page_cache_hours * 3600:
            return Page(url, 200, row["body"], True)
        for _ in range(hops + 1):
            self.refuse(url)
            host = host_of(url)
            self.lim.check("web", host, extra_caps)
            if not self.robots.allowed(url):
                st = self.db.execute("SELECT status FROM robots WHERE domain=?", (urlparse(url).netloc.lower(),)).fetchone()
                if st and st["status"] != 200:
                    raise Blocked(f"robots.txt for {host} unreachable (status {st['status']}), treated as disallow")
                raise Blocked(f"robots.txt disallows {url}")
            headers = {}
            if row and row["url"] == url:
                if row["etag"]:
                    headers["If-None-Match"] = row["etag"]
                if row["last_modified"]:
                    headers["If-Modified-Since"] = row["last_modified"]
            delay = self.robots.crawl_delay(host)
            if delay:
                last = self.db.execute("SELECT last_at FROM rate_state WHERE bucket='web' AND key=?", (host,)).fetchone()
                if last and last["last_at"] and last["last_at"] + delay > self.lim.clock():
                    self.lim.sleep(last["last_at"] + delay - self.lim.clock())
            self.lim.acquire("web", host, url, extra_caps)
            try:
                r = self.http.get(url, headers=headers)
            except httpx.HTTPError as ex:
                log.warning("fetch failed %s: %s", url, type(ex).__name__)
                self.lim.failed("web", host, type(ex).__name__)
                return Page(url, 0, "")
            self.lim.log_status("web", host, r.status_code)
            if r.status_code in (403, 429):
                until = self.lim.trip("web", host, f"HTTP {r.status_code}")
                raise InCooldown("web", host, until, f"HTTP {r.status_code}")
            if r.status_code >= 500:
                self.lim.failed("web", host, f"HTTP {r.status_code}")
                return Page(url, r.status_code, "")
            self.lim.succeeded("web", host)
            if r.status_code == 304 and row:
                self.db.execute("UPDATE pages SET fetched_at=? WHERE url=?", (self.lim.clock(), url))
                return Page(url, 200, row["body"], True)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                row = self.db.execute("SELECT * FROM pages WHERE url=?", (url,)).fetchone()
                continue
            if r.encoding is None or r.encoding.lower() in ("iso-8859-1", "ascii"):
                r.encoding = r.charset_encoding or "utf-8"
            text = r.text[: self.s.limits.web.max_bytes]
            if r.status_code == 200:
                self.db.execute("INSERT OR REPLACE INTO pages(url, fetched_at, status, etag, last_modified, body) "
                                "VALUES(?,?,?,?,?,?)", (url, self.lim.clock(), 200, r.headers.get("etag"),
                                                        r.headers.get("last-modified"), text))
            return Page(url, r.status_code, text)
        return Page(url, 0, "")
