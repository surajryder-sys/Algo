---
name: project-tv-scraper-chart-timezone
description: "User's explicit standing rule: ALWAYS report times in IST (UTC+5:30) in chat, for anything -- zone/candle times, MT5 deal times, process restart times, log timestamps, everything -- never bare UTC"
metadata: 
  node_type: memory
  type: project
  originSessionId: beca6970-4dba-4bca-8c27-e6b6f4892269
  modified: 2026-08-21T17:26:26.698Z
---

**Standing instruction, user's own words (2026-08-21): "always give me in
IST."** This applies to EVERY time reported in chat for this project, not
just chart/zone times -- MT5 deal timestamps, process restart times
(`create_time`), log line timestamps, everything. Convert to IST before
presenting; don't present bare UTC and expect the user to convert.
`datetime.fromtimestamp()` (no explicit UTC) already returns IST on this
machine (confirmed empirically), so MT5 query results via
`mt5.history_deals_get()` etc. are already IST as-is -- only raw epoch
values reconstructed manually (e.g. via `datetime.utcfromtimestamp()`,
zone `start_time`/`event_time` fields) need the explicit +5:30 conversion
before reporting.

The TradingView chart `v3/tv_scraper/scraper.py` polls (BTCUSD, dual-pane
grid, r0c0/r0c1) is set to display candle times in **India Standard Time
(UTC+5:30)**, confirmed empirically live: a candle reconstructed as
16:56 UTC (from Pine's `minutes_since_ref` field, see
[[project_ob_mtf_bot_strategy]]) matched the user's on-chart time of
~22:26 IST exactly (16:56 + 5:30 = 22:26).

**Why:** All timestamp reconstruction in `scraper.py`
(`_reconstruct_hint()`, `_REF_EPOCH_UTC`) is done in UTC — that's the
correct internal representation and shouldn't change. But when
reporting zone formed/retested times back to the user in chat, they
need to be converted to IST (+5:30) first, or the user will see an
apparent "mismatch" against their own chart (this happened once and
required 3 clarifying questions to diagnose — it was a display
convention gap, not a bug in the reconstruction).

**How to apply:** When giving "bias and zone details" with candle
times, always add 5:30 to the reconstructed UTC time and label it IST,
rather than presenting raw UTC. If the user ever changes their chart's
timezone setting, this offset would need re-verifying the same way
(pick one already-reported candle, ask what time it shows on-chart now).
