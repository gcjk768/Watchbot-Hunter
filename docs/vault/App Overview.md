---
tags: [active]
updated: 2026-10-03
---
# App Overview

**Live:** NAS stack `/volume1/docker/watchbot` (container `watchbot`), bot @jameskoh_watchbot → James Channel topic 4164. Alerts DM 388437106. Runtime vault `/volume1/James/Obsidian/Watchbot`.

## Jobs (`watchbot/serve.py`)
| When (SGT) | Job | Claude? |
|---|---|---|
| hourly :07 | eBay SG collect → `watchbot/market.py` values → 💎 card for **new** deals only | no |
| 06:30 | discovery: one `claude -p` web search (sonnet) for SG listings + news (`watchbot/ai.py`) | yes |
| 08:00 | lesson, topic rotation (haiku) (`watchbot/ai.py`) | yes |
| 08:30 | 📈 market card (`watchbot/cards.py`) | no |

## Commands (owner only, own topic or DM; unique `/watch*` names)
`/watchhelp /watchmarket /watchdeals /watchlist /watchadd REF Brand Model /watchdel REF /watchstatus`. Buttons whitelist: market, deals, status.

## Deal maths (`watchbot/market.py`)
Market = trimmed median of 30-day SG asks × 0.95; needs ≥5 asks ("thin" otherwise, never a deal). Landed = price + delivery + payment + insurance + authentication + service reserve. Best exit channel from `config.yaml` `costs`. Deal = net ≥ S$500 and margin ≥ 8%; each listing alerts once (`deals` table).

## Deploy
`sh deploy.sh` (git archive → NAS, keeps `.env` and `data/`, `compose up -d --build`). Test card: `docker compose exec watchbot watchbot hello`.
