---
name: project-virgin-zone-telegram-alerts
description: Telegram alert feature that WAS built for the old XAUUSD algo bot (now deleted) -- code is gone, credentials still live, nothing currently sends alerts
metadata: 
  node_type: memory
  type: project
  originSessionId: 21a14b48-dd35-44c9-8824-cfb1d90a3bfa
  modified: 2026-08-17T09:36:25.725Z
---

**STALE AS OF 2026-08-17 -- the code described below no longer exists.**
`algo/` (along with `btc_smc/`/`eth_smc/`) was deleted as part of the old
reversal system's full removal (see [[project_ob_mtf_bot_strategy]]) --
confirmed live via repo-wide grep, no `.py` file references
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` anymore. Nothing currently sends
Telegram alerts for anything. The credentials themselves are still live
in `.env` and still work (see below) -- only the CODE that called them
is gone. Kept the historical build notes below for the implementation
pattern (`AlertedZoneStore`, entry-only firing, urllib not requests,
etc.) since that's directly reusable for building this fresh against
the current `v3/tv_scraper` zone data -- see [[project_tv_scraper_multi_symbol_setup]].

Original (2026-07-28) build notes, describing the NOW-DELETED `algo/`:
a Telegram alert fired when price traded into a still-virgin OB zone on
H4/H2/H1/M30/M15 for the XAUUSD bot (`algo/` in `C:\Users\ARK\projects\Algo`).

**What was built**: `algo/alerts.py` (new module) reads the OB bridge independently of the M15/M5/M3/M1 the trading logic itself uses, checks H4/H2/H1/M30/M15 bull+bear zone history each poll, and sends one Telegram message the moment price enters a zone that's still `virgin`. `AlertedZoneStore` persists fired zone keys to `smc_bot_alerts.json` so a restart doesn't repeat. Wired into `algo/main.py`'s `run_once`, gated inside the existing `if cfg.enable_trading:` block — alerts only fire when `SMC_ENABLE_TRADING=true` (by explicit user choice, not always-on).

**Decisions made (ask before assuming otherwise if revisiting)**:
- Entry-only alert, no separate "approaching" pre-alert — user explicitly rejected a distance/percentage threshold in favor of firing only on actual [low, high] entry.
- Timeframe scope is H4/H2/H1/M30/M15 specifically (not M5/M3/M1, which are the trading bot's own entry timeframes) — user's deliberate choice.
- XAUUSD (`algo/`) only for now — user chose "just one symbol first" over doing all three bots at once. BTCUSD (`btc_smc/`) and ETHUSD (`eth_smc/`) do NOT have this feature; extending them would need the same `alerts.py` pattern copied into those packages with their own Config wired in.
- Telegram was built first; MT5 mobile push (`SendNotification()`) was explicitly deferred as a separate follow-up — it can only be done from the MQL5 indicator side (`mql5/OB_StatePublisher_Indicator_v2.00.mq5`), not from Python, since the `MetaTrader5` python package has no equivalent call.
- No `requests` package was installed in this environment/repo at the time — `algo/alerts.py` used stdlib `urllib` instead. **Re-checked 2026-08-17: `requests` IS installed now** — fine to use directly for any fresh implementation instead of matching the old urllib pattern.

**Credentials**: Telegram bot `@SmcSecret_bot`, token and chat ID (redacted -- see .env) are both live in `.env` (git-ignored) as `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`. Verified end-to-end with a real test send on 2026-07-28.

**Gotcha hit and fixed**: the first draft put an emoji directly in a `print()` call for the console log line. Windows' default console encoding (cp1252) can't encode emoji and raised `UnicodeEncodeError`, which — because `algo/main.py`'s per-cycle exception handler wraps the *entire* `run_once` call — would have silently skipped that whole poll's trailing-SL/exit/entry logic, not just the alert. Fixed by keeping the console `print()` ASCII-only and only putting the emoji in the Telegram-bound text (sent as UTF-8 bytes over HTTP, never printed to console). **How to apply**: never print non-ASCII characters to console in this codebase without checking encoding safety first, especially inside code paths that run under `algo/main.py`'s broad per-cycle try/except — an unrelated crash there has a wider blast radius than it looks.

**Why**: user wants informational awareness of untested OB zones being entered, without adding a "near-miss" alert (rejected as noise) and without expanding scope to BTC/ETH bots before XAUUSD is proven out.

**How to apply**: if asked to extend this to `btc_smc`/`eth_smc`, reuse `algo/alerts.py`'s pattern (same `AlertedZoneStore`/`check_virgin_zone_alerts` shape) rather than redesigning, and confirm timeframe scope + whether it shares the same Telegram bot/chat or needs its own before building. If asked about MT5 mobile push, remember it requires MQL5-side `SendNotification()` plus the user registering their MetaQuotes ID in the terminal's Options (a GUI step they do themselves).
