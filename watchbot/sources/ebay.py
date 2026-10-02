"""eBay Browse API, Singapore only: client credentials token, item_summary/search on EBAY_SG with itemLocationCountry:SG.

Sold prices need Marketplace Insights, which eBay restricts, so it is never assumed. collect.py instead records
every listing each day and labels one that ends early or disappears as "likely sold", never "sold".
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime

import httpx

from .base import Listing, condition_from_text, detect_set, detect_year, mentions_ref, service_from_text

log = logging.getLogger(__name__)
API = "https://api.ebay.com"
SCOPE = "https://api.ebay.com/oauth/api_scope"
# eBay condition ids: https://developer.ebay.com/api-docs/sell/static/metadata/condition-id-values.html
CONDITION_IDS = {"1000": "new", "1500": "new", "2750": "excellent", "2990": "excellent", "3000": "good",
                 "3010": "good", "4000": "good", "5000": "good"}
EXCLUDED_IDS = {"1750", "7000"}   # new with defects, for parts or not working


class EbayError(Exception):
    pass


class Ebay:
    source = "ebay"

    def __init__(self, db, lim, s, transport: httpx.BaseTransport | None = None):
        self.db, self.lim, self.s = db, lim, s
        self.cfg = s.sources.ebay
        self.http = httpx.Client(base_url=API, timeout=30, transport=transport,
                                 headers={"User-Agent": s.limits.web.user_agent})

    @property
    def configured(self) -> bool:
        return bool(self.s.secrets.ebay_client_id and self.s.secrets.ebay_client_secret)

    def token(self) -> str:
        row = self.db.execute("SELECT value, expires_at FROM kv WHERE key='ebay_token'").fetchone()
        if row and row["expires_at"] > self.lim.clock() + 120:
            return row["value"]
        basic = base64.b64encode(f"{self.s.secrets.ebay_client_id}:{self.s.secrets.ebay_client_secret}".encode()).decode()
        self.lim.acquire("ebay", "oauth", "/identity/v1/oauth2/token")
        r = self.http.post("/identity/v1/oauth2/token", data={"grant_type": "client_credentials", "scope": SCOPE},
                           headers={"Authorization": f"Basic {basic}",
                                    "Content-Type": "application/x-www-form-urlencoded"})
        self.lim.log_status("ebay", "oauth", r.status_code)
        if r.status_code != 200:
            raise EbayError(f"oauth: HTTP {r.status_code} {r.text[:200]}")
        d = r.json()
        self.db.execute("INSERT OR REPLACE INTO kv(key, value, expires_at) VALUES('ebay_token',?,?)",
                        (d["access_token"], self.lim.clock() + int(d.get("expires_in", 7200))))
        return d["access_token"]

    def search(self, ref: dict, max_pages: int = 2) -> tuple[list[Listing], bool, int]:
        """Listings for one reference. Returns (listings, complete, raw_count); complete is False when the result
        was cut short, so collect.py does not treat missing listings as ended."""
        q = f"{ref['brand']} {ref['ref']}".strip()
        params = {"q": q, "category_ids": self.cfg.category_id, "limit": str(self.cfg.page_size),
                  "filter": f"itemLocationCountry:{self.cfg.item_location}"}
        headers = {"Authorization": f"Bearer {self.token()}", "X-EBAY-C-MARKETPLACE-ID": self.cfg.marketplace_id,
                   "X-EBAY-C-ENDUSERCTX": f"contextualLocation=country={self.cfg.item_location}",
                   "Accept-Language": "en-SG"}
        out, raw_n, offset, complete = [], 0, 0, True
        for page in range(max_pages):
            if self.lim.left("ebay") <= 0:
                return out, False, raw_n
            self.lim.acquire("ebay", "browse", f"search {q} offset {offset}")
            r = self.http.get("/buy/browse/v1/item_summary/search", params={**params, "offset": str(offset)},
                              headers=headers)
            self.lim.log_status("ebay", "browse", r.status_code)
            if r.status_code == 401 and page == 0:
                self.db.execute("DELETE FROM kv WHERE key='ebay_token'")
                headers["Authorization"] = f"Bearer {self.token()}"
                continue
            if r.status_code in (429, 403):
                self.lim.trip("ebay", "browse", f"HTTP {r.status_code}")
                raise EbayError(f"search: HTTP {r.status_code}, eBay cooling down")
            if r.status_code != 200:
                self.lim.failed("ebay", "browse", f"HTTP {r.status_code}")
                raise EbayError(f"search: HTTP {r.status_code} {r.text[:200]}")
            self.lim.succeeded("ebay", "browse")
            data = r.json()
            items, ls = parse_search(data, ref, self.s)
            raw_n += items
            out += ls
            total = int(data.get("total") or 0)
            offset += int(self.cfg.page_size)
            if offset >= total or not data.get("next"):
                break
        else:
            complete = False
        return out, complete, raw_n


def _money(m: dict | None) -> tuple[float | None, str | None]:
    if not m or m.get("value") in (None, ""):
        return None, None
    return float(m["value"]), m.get("currency")


def parse_item(it: dict, ref: dict, now_year: int) -> Listing | None:
    """One itemSummary -> Listing, or None when it is not this reference or not a whole working watch."""
    title = it.get("title") or ""
    if not mentions_ref(title, ref["ref"], ref.get("aliases") or ()):
        return None
    cid = str(it.get("conditionId") or "")
    if cid in EXCLUDED_IDS:
        return None
    price, cur = _money(it.get("price"))
    if price is None and it.get("currentBidPrice"):
        price, cur = _money(it.get("currentBidPrice"))
    loc = it.get("itemLocation") or {}
    seller = it.get("seller") or {}
    ship, ship_cur = None, None
    for opt in it.get("shippingOptions") or []:
        ship, ship_cur = _money(opt.get("shippingCost"))
        if ship is not None:
            break
    if ship is not None and ship_cur and cur and ship_cur != cur:
        ship = None   # different currency, do not mix
    text = " ".join(filter(None, [title, it.get("shortDescription") or "", it.get("condition") or ""]))
    imgs = [i.get("imageUrl") for i in [it.get("image") or {}, *(it.get("additionalImages") or [])] if i.get("imageUrl")]
    acct = (seller.get("sellerAccountType") or "").upper()
    fb = {}
    if seller.get("feedbackScore") is not None:
        fb["score"] = int(seller["feedbackScore"])
    if seller.get("feedbackPercentage") not in (None, ""):
        fb["pct"] = float(seller["feedbackPercentage"])
    country = (loc.get("country") or "").upper() or None
    return Listing(
        source="ebay", source_id=str(it.get("itemId") or it.get("legacyItemId")), ref=ref["ref"], title=title,
        price=price, currency=cur, url=it.get("itemWebUrl") or "", site="ebay.com.sg",
        condition=CONDITION_IDS.get(cid) or condition_from_text(text), condition_text=it.get("condition") or "",
        year=detect_year(title, now_year), full_set=detect_set(text),
        seller_type="dealer" if acct == "BUSINESS" else "private" if acct == "INDIVIDUAL" else None,
        seller_name=seller.get("username"),
        # the Browse summary has no seller country; the item location is where the seller ships from
        seller_country=country, item_country=country,
        area=", ".join(filter(None, [loc.get("city"), "eBay"])) or "eBay",
        feedback=fb, shipping=ship, buying_options=list(it.get("buyingOptions") or []), images=imgs,
        scheduled_end=it.get("itemEndDate"), service_record=service_from_text(text), text=text,
        raw={k: it.get(k) for k in ("itemId", "title", "price", "condition", "conditionId", "itemLocation", "seller",
                                    "shippingOptions", "buyingOptions", "itemWebUrl", "itemEndDate", "image")},
    )


def parse_search(data: dict, ref: dict, s=None) -> tuple[int, list[Listing]]:
    now_year = datetime.now().year
    items = data.get("itemSummaries") or []
    out = [l for l in (parse_item(it, ref, now_year) for it in items) if l]
    return len(items), out


def dumps(l: Listing) -> str:
    return json.dumps(l.raw, ensure_ascii=False)
