# watchbot

A small scheduled bot for learning the Singapore luxury watch market: market values by reference, deals after every cost, paper trading, one lesson a day. Telegram is the daily feed; the Obsidian vault is the record.

Live on the NAS (Docker stack `watchbot`), posting to James Channel topic 4164 as @jameskoh_watchbot. Hourly eBay SG collect with a card for new deals only, daily discovery (06:30), lesson (08:00) and market card (08:30). Commands: `/watchhelp /watchmarket /watchdeals /watchlist /watchadd /watchdel /watchstatus`. Docs: `docs/vault/`. Deploy: `sh deploy.sh`.

## Sources: robots.txt and terms check (2026-10-02)

The build environment could not open most of these sites (its network policy blocked them), so the findings below come from search results that quote the pages. Each one needs a manual recheck from the NAS. The bot fails safe: a source whose terms are not confirmed as allowing automated access is not fetched. It stays reachable only through discovery snippets labelled "from search". Verdicts live in `rules.yaml`; change a verdict only after reading the terms yourself.

| Source | Verdict | What the bot does |
|---|---|---|
| eBay Browse API | allowed (official API) | `item_summary/search` on marketplace `EBAY_SG` with `filter=itemLocationCountry:SG`, client credentials token, at most `daily_calls_ebay` calls (eBay's default is 5000 a day). Sold prices need Marketplace Insights, which is a limited release API, so it is not used. Listings that end early or disappear are labelled "likely sold". |
| Chrono24 | unverified | robots.txt and the terms clause could not be read. Bot detection is in place. Not fetched; discovery snippets only, until you read the terms and set `sources.chrono24.verdict: allowed`. |
| Carousell | forbidden | The Terms of Service forbid any scraper, robot, bot, spider, crawler or other automated means to access any portion of the Services. So the bot never fetches carousell.sg, not even a link you send with /check. Send pasted text or a screenshot instead. |
| WatchCharts website | forbidden | Terms limit data to personal use and to means the site makes available. Only the paid API is used, and only when `WATCHCHARTS_API_KEY` is set. |
| Brand sites (retail) | robots.txt decides | `retail.py` re-reads each `retail_url` weekly through the polite fetcher and takes a price only when the page states it in SGD. Otherwise it keeps the figure verified during the build, with its date. |
| Singapore dealers | unverified | Nine candidates are listed in `rules.yaml` with a quality score. robots.txt is checked automatically, but a dealer is fetched only after you set `terms: allowed` for it. |

## Price labels

* **asking**: a listing's price.
* **sold**: only when the source shows a completed sale.
* **likely sold**: a listing that ended early or disappeared.
* **market**: a computed reference value.
* **retail**: the brand's Singapore list price, with its date.

## Quick start (development)

```
uv venv -p 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/pytest
cp .env.example .env    # fill in the keys
watchbot watchlist      # the watchlist with retail prices
watchbot backfill 1     # first slow collect, then the watchlist with eBay SG counts
watchbot status
```
