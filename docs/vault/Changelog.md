---
tags: [active]
updated: 2026-10-03
---
# Changelog

## 2026-10-03
- feat: cards follow the SG car tracker layout (`watchbot/cards.py`): numbered `emoji n. <a>Name</a> · ref 🆕 NEW` heads, dot-joined lines, `$` money, ▲/▼ deltas, notes collapsed last; 🔄 Run again / 💎 Deals / 📈 Market buttons; `/watchlistings`
- fix: discovery keeps a listing only when its URL names the reference and is not another country's locale (`buyable` in `watchbot/ai.py`); haiku returned category pages and en-MY/en-VN pages
- operating hours 05:00 to 21:00 SGT, avoiding the trading desk US session; discovery 5x/day, claude cap 8
- feat: 🆕 new-listing alerts the moment they are found, once each (`alerted` table); discovery every 3 h 24/7; claude cap 12/day
- feat: 🛒 Buy at <shop> purchase link on every listing and deal card; site line shows the shop domain
- feat: `watchbot sample N` posts N listing cards (`watchbot/cli.py`, `watchbot/cards.py` listing_block)
- every claude -p call on haiku (discovery too); hourly collect stays 24/7 like the fleet
- replaced pddbot in the owner Channel (pddbot container removed); bot admin in topic 4164
- feat: market values, deal alerts, discovery, lessons, Telegram listener (`watchbot/serve.py`, `watchbot/market.py`, `watchbot/ai.py`, `watchbot/cards.py`, `watchbot/vault.py`)
- feat: Dockerfile, docker-compose.yml, deploy.sh; deployed to NAS, bot @owner_watchbot → topic 4164
- fix: `claude.py` drops `--fallback-model` when it equals `--model` (CLI refuses it)
- schedule hourly collect (fleet policy), haiku for lessons, sonnet only for discovery

## 2026-10-02
- steps 1 and 2: scaffold, limits, web, Telegram, Claude wrapper, eBay SG, FX, retail, watchlist
