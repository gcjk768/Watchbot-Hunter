---
tags: [active]
updated: 2026-10-03
---
# Changelog

## 2026-10-03
- feat: market values, deal alerts, discovery, lessons, Telegram listener (`watchbot/serve.py`, `watchbot/market.py`, `watchbot/ai.py`, `watchbot/cards.py`, `watchbot/vault.py`)
- feat: Dockerfile, docker-compose.yml, deploy.sh; deployed to NAS, bot @owner_watchbot → topic 4164
- fix: `claude.py` drops `--fallback-model` when it equals `--model` (CLI refuses it)
- schedule hourly collect (fleet policy), haiku for lessons, sonnet only for discovery

## 2026-10-02
- steps 1 and 2: scaffold, limits, web, Telegram, Claude wrapper, eBay SG, FX, retail, watchlist
