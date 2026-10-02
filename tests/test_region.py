import pytest

from watchbot import region
from watchbot.sources.base import Listing


def L(source, url, seller=None, item=None):
    return Listing(source=source, source_id="1", ref="126610LN", title="t", price=1, currency="SGD", url=url,
                   seller_country=seller, item_country=item)


@pytest.mark.parametrize("listing,keep", [
    (L("ebay", "https://www.ebay.com.sg/itm/1", "SG", "SG"), True),
    (L("ebay", "https://www.ebay.com.sg/itm/1", "MY", "MY"), False),
    (L("ebay", "https://www.ebay.com/itm/1", "US", "US"), False),
    (L("ebay", "https://www.ebay.com.sg/itm/1", None, None), False),
    (L("ebay", "https://www.ebay.com.sg/itm/1", "SG", "HK"), False),
    (L("chrono24", "https://www.chrono24.com/rolex/x--id1.htm", "SG", "SG"), True),
    (L("chrono24", "https://www.chrono24.com/rolex/x--id1.htm", "DE", "DE"), False),
    (L("chrono24", "https://www.chrono24.sg/rolex/x--id1.htm", "HK", None), False),
    (L("sg_dealers", "https://watchexchange.sg/products/x"), True),
    (L("sg_dealers", "https://horologymaison.com/products/x"), True),
    (L("sg_dealers", "https://watchexchange.sg/products/x", "MY", "MY"), False),
    (L("sg_dealers", "https://www.bobswatches.com/x"), False),
    (L("discover", "https://www.somedealer.sg/x"), True),
    (L("discover", "https://www.somedealer.com.my/x"), False),
    (L("discover", "https://www.somedealer.com/x", "SG"), True),
    (L("discover", "https://www.somedealer.com/x", "US"), False),
    (L("manual", "https://www.carousell.sg/p/x", "SG", "SG"), True),
    (L("manual", "https://www.carousell.com.my/p/x", "MY", "MY"), False),
])
def test_singapore_only(s, listing, keep):
    assert region.check(listing, s)[0] is keep


def test_keep_sg_splits_and_says_why(s):
    kept, dropped = region.keep_sg([L("ebay", "https://www.ebay.com.sg/itm/1", "SG", "SG"),
                                    L("ebay", "https://www.ebay.com.sg/itm/2", "MY", "MY")], s)
    assert len(kept) == 1 and "MY" in dropped[0][1]
