---
tags: [active]
updated: 2026-10-03
---
# App Overview

**Live:** NAS stack `/volume1/docker/watchbot` (container `watchbot`), bot @owner_watchbot → the owner Channel topic 4164. Alerts DM 388437106. Runtime vault `/volume1/<USER>/Obsidian/Watchbot`.

## Jobs (`watchbot/serve.py`)
**Operating hours 05:00 to 21:00 SGT** (the owner, 2026-10-03): quiet while the trading desk runs the US session (21:30 to 04:00 SGT, 22:30 to 05:00 after US DST ends) so the shared Claude plan and the channel stay with the desk. Commands still answer any time. `/watchhelp` says the same; crons in `config.yaml`.
| When (SGT) | Job | Claude? |
|---|---|---|
| hourly :07, 05 to 20 | eBay SG collect → `watchbot/market.py` values → 💎 card for **new** deals only | no |
| 06:30 09:30 12:30 15:30 18:30 | discovery: one `claude -p` web search (haiku) for SG listings + news (`watchbot/ai.py`) | yes |
| 08:00 | lesson, topic rotation (haiku) (`watchbot/ai.py`) | yes |
| 08:30 | 📈 market card (`watchbot/cards.py`) | no |

New finds post the moment a job sees them (inside operating hours): 💎 deal cards first, then 🆕 listing cards for every listing never sent before (`alerted` table, `new_finds` in `watchbot/serve.py`). Every listing and deal card ends with a 🛒 **Buy at <shop>** link.

## Commands (owner only, own topic or DM; unique `/watch*` names)
`/watchhelp /watchlistings /watchmarket /watchdeals /watchlist /watchadd REF Brand Model /watchdel REF /watchstatus`. Buttons (last message): 🔄 Run again (/watchlistings), 💎 Deals, 📈 Market; whitelist is those plus status. Message layout copies @owner_sgcar_bot (`watchbot/cards.py`).

## Deal maths (`watchbot/market.py`)
Market = trimmed median of 30-day SG asks × 0.95; needs ≥5 asks ("thin" otherwise, never a deal). Landed = price + delivery + payment + insurance + authentication + service reserve. Best exit channel from `config.yaml` `costs`. Deal = net ≥ S$500 and margin ≥ 8%; each listing alerts once (`deals` table).

## Test
`docker compose exec watchbot watchbot sample 5` posts 5 Singapore listings as watch cards (runs one discovery call first when fewer than 5 are stored).

## Deploy
`sh deploy.sh` (git archive → NAS, keeps `.env` and `data/`, `compose up -d --build`). Test card: `docker compose exec watchbot watchbot hello`.

All `claude -p` calls run on **haiku** (the owner, 2026-10-03); `config.yaml` `claude` section.
