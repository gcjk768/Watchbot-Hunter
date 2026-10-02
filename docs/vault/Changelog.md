---
tags: [active]
updated: 2026-10-03
---
# Changelog

## 2026-10-03
- feat: `watchbot sample N` posts N listing cards (`watchbot/cli.py`, `watchbot/cards.py` listing_block)
- every claude -p call on haiku (discovery too); hourly collect stays 24/7 like the fleet
- replaced pddbot in the owner Channel (pddbot container removed); bot admin in topic 4164
- feat: market values, deal alerts, discovery, lessons, Telegram listener (`watchbot/serve.py`, `watchbot/market.py`, `watchbot/ai.py`, `watchbot/cards.py`, `watchbot/vault.py`)
- feat: Dockerfile, docker-compose.yml, deploy.sh; deployed to NAS, bot @owner_watchbot → topic 4164
- fix: `claude.py` drops `--fallback-model` when it equals `--model` (CLI refuses it)
- schedule hourly collect (fleet policy), haiku for lessons, sonnet only for discovery

## 2026-10-02
- steps 1 and 2: scaffold, limits, web, Telegram, Claude wrapper, eBay SG, FX, retail, watchlist
