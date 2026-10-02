from watchbot.sources.retail import parse_price_sgd


def test_json_ld_sgd():
    html = '<script type="application/ld+json">{"@type":"Product","offers":{"price":"15950","priceCurrency":"SGD"}}</script>'
    assert parse_price_sgd(html) == 15950


def test_other_currency_is_never_taken():
    html = '<script type="application/ld+json">{"@type":"Product","offers":{"price":"4790","priceCurrency":"USD"}}</script>'
    assert parse_price_sgd(html) is None


def test_meta_tags():
    html = '<meta property="product:price:amount" content="8,700.00"><meta property="product:price:currency" content="SGD">'
    assert parse_price_sgd(html) == 8700


def test_no_price():
    assert parse_price_sgd("<html><body>Price on request</body></html>") is None
