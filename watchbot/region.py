"""Rule 9, Singapore only. A listing is kept when it is on a Singapore site (a .sg domain or a known Singapore
dealer) and nothing says it is elsewhere, or on a platform where both the seller and the item are in Singapore."""
from __future__ import annotations

from .sources.base import Listing
from .web import host_of, matches

SG_NAMES = {"SG", "SGP", "SINGAPORE"}


def country(code: str | None) -> str | None:
    if not code:
        return None
    c = code.strip().upper()
    return "SG" if c in SG_NAMES else c


def sg_dealer_domains(s) -> list[str]:
    return list((s.rules or {}).get("sg_dealers", {}).keys())


def is_sg_site(url: str, s) -> bool:
    host = host_of(url)
    return any(host.endswith(t) for t in s.region.allowed_tlds) or matches(host, sg_dealer_domains(s))


def check(listing: Listing, s) -> tuple[bool, str]:
    seller, item = country(listing.seller_country), country(listing.item_country)
    for c, what in ((seller, "seller"), (item, "item")):
        if c and c != "SG":
            return False, f"{what} is in {c}"
    if listing.source in ("ebay", "chrono24"):
        # a platform: whatever the domain, both the seller and the item must be shown in Singapore
        if seller != "SG" or item != "SG":
            return False, "platform listing not shown as seller and item in Singapore"
        return True, "seller and item in Singapore"
    if is_sg_site(listing.url, s):
        return True, "Singapore site"
    if listing.source == "discover" and s.region.allow_non_sg_domains_only_for_sg_sellers and seller == "SG":
        return True, "Singapore seller on a non .sg site"
    if listing.source == "manual" and not listing.url:
        return True, "sent by you, nothing says it is outside Singapore"
    return False, "not a Singapore site or seller"


def keep_sg(listings: list[Listing], s) -> tuple[list[Listing], list[tuple[Listing, str]]]:
    kept, dropped = [], []
    for l in listings:
        ok, why = check(l, s)
        (kept.append(l) if ok else dropped.append((l, why)))
    return kept, dropped
