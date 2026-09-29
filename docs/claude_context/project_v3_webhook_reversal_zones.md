---
name: project-v3-webhook-reversal-zones
description: "v3 Sentinel: webhook migration for Reversal Manager's zone/retest data is now ACTIVELY IN PROGRESS (separate session) -- Reversal Manager itself is deliberately shut OFF pending a real unexplained hang tied to this work"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7aad8755-8676-4c62-ae5f-809edd8c2884
  modified: 2026-08-20T20:26:31.301Z
---

**STATUS UPDATE 2026-08-20 (later same day, different session than the
one below) -- Reversal Manager is DELIBERATELY STOPPED, do not restart
it without checking with the user first.** The separate session doing
this webhook migration has made real, substantial progress (uncommitted,
in-progress): `reversal_config.py` now points ALL FIVE symbols'
zone_state_file/atr_state_file at ONE shared webhook-fed file each
(`tradingview_bot_zones.json`/`tradingview_bot_atr.json`, env vars
`TV_ZONE_STATE_FILE`/`TV_ATR_STATE_FILE`) instead of tv_scraper's
per-symbol files; `AtrStore` gained multi-period support
(`atr_period` field, `get_all_for()`); a live BTCUSD-only webhook test
feed is actively running (10-second/30-second bar zones, visible in
`tradingview_bot_run.log`, firing every 1-2 minutes) -- `pine/OBD_ATR.pine`
and `pine/OBD_Reversal.pine` (renamed from the old `atr_trail_webhook.pine`/
`ob_detector_webhook.pine`) are the current Pine scripts.

While testing this, `v3/signal_engine/reversal_manager.py`'s `main()`
developed a genuine, 100%-reproducible hang (confirmed via CPU sampling
across many independent tests -- background, foreground, a completely
fresh PowerShell process, all identical: zero CPU, stuck forever right
after printing the startup line). Extensively investigated and could
NOT pin the exact cause: ruled out shell/session degradation, file
locking (none exists in zone_store.py/atr_store.py), file size (shared
files are tiny, under 4KB). A hand-written loop replicating main()'s
exact structure (same imports/objects, same try/except/sleep) never
hangs; only calling the real `main()` does, every time. Best remaining
theory (unproven): tied to the shared zone/ATR file being rewritten so
frequently by the live test feed, though no locking mechanism exists to
explain an actual block, not just a transient parse error (which IS
already handled gracefully). User's decision: leave Reversal Manager
OFF until this webhook work is finished/stabilized, rather than keep
blind-restarting it. Trend Manager, Execution Bridge, and all 4
tv_scraper processes are unaffected and were confirmed healthy at the
same time -- this is scoped to Reversal Manager specifically.

**Original plan/investigation below (2026-08-20 morning, when this was
still "not yet started" from Trend Manager's own signal-engine session)
-- now superseded by the above, kept for historical context on WHY the
migration was wanted in the first place:**

2026-08-20: user wants to set up TradingView webhook alerts specifically
for Reversal Manager's zone data (timeframe, direction, top/btm,
retest time) instead of relying on tv_scraper's Data Window polling --
motivation: polling can miss a retest that happens between polls, or
misread `retested_at == start_time` (a known Pine array-slot-reuse
artifact tv_scraper is already vulnerable to -- Reversal Manager
currently just distrusts and drops any zone with that signature rather
than being able to tell a real same-instant retest apart from the
artifact). Also wants a NEW invalidation rule: once live price crosses
beyond a zone's own top/btm, delete that zone outright from the
reversal-side history (not currently built anywhere).

User wants to continue this specific work in a SEPARATE chat/session --
this note exists so that session doesn't have to rediscover the
following from scratch.

**What already exists in the repo (built in an earlier session, currently
dormant, not running):**
- `v3/tv_bridge/receiver.py` -- HTTP webhook server. Already accepts an
  `ob_zone_retested` payload with exactly the fields wanted: symbol,
  timeframe, direction, start_time, retested_time (plus `ob_zone_formed`
  or top/btm, `ob_zone_mitigated`). Requires `TV_WEBHOOK_SECRET` in
  requests (shared-secret in the JSON body, not a header, since
  TradingView alerts can't send custom headers). Binds locally only
  (`TV_WEBHOOK_HOST`/`TV_WEBHOOK_PORT`) -- needs a tunnel (Cloudflare
  Tunnel / ngrok) for TradingView's real servers to reach it.
- `.env` already has `TV_WEBHOOK_SECRET`, `TV_WEBHOOK_HOST=127.0.0.1`,
  `TV_WEBHOOK_PORT=8765`, `TV_SIGNAL_LOG_FILE=tv_bridge_signals.jsonl`
  set from whenever this was last touched -- unknown whether a tunnel
  was ever actually stood up; that's the first thing to check in the
  new session.
- `v3/tradingview_bot/main.py` -- reads new lines from the webhook
  signal log, folds `ob_zone_formed`/`ob_zone_mitigated`/
  `ob_zone_retested` into `ZoneStore` (the SAME class Reversal Manager
  already reads from via `v3/tradingview_bot/zone_store.py`), and
  `atr_trail` into `AtrStore`. Not currently running.
- `ZoneStore.apply_retested()` already exists and is specifically
  documented as using Pine's own exact bar-time knowledge, "not
  tv_scraper's own live-Close approximation."

**What's missing:**
1. `pine/ob_detector_webhook.pine` sends `ob_zone_formed`/
   `ob_zone_mitigated` alerts but has NO `ob_zone_retested` alert() call
   at all -- grepped, confirmed absent. Needs adding (Pine already has
   the mitigation/formation alert pattern to copy from in the same
   file).
2. Nothing is running: receiver, tradingview_bot.main both dormant.
3. Tunnel status unknown -- needs checking/setting up.
4. Real TradingView alerts need creating on the relevant chart(s),
   pointed at the tunnel URL, using this Pine script.
5. Real decision needed: should Reversal Manager read this webhook-fed
   ZoneStore INSTEAD of tv_scraper's own, or MERGE both at read time?
   (Precedent for merging exists elsewhere in this repo --
   algo_v2_tv_xauusd/reader.py merges two sources at read time.)
6. New code needed, doesn't exist anywhere yet: "once live price
   crosses beyond a zone's own top/btm, delete the zone" -- an
   independent invalidation check against live price (from tv_scraper's
   or MT5's own live feed), separate from waiting on Pine's own
   `ob_zone_mitigated` alert.

See [[project_v3_crypto_architecture]] for the overall V3 Sentinel
component map this slots into (Data Bridge -> Signal Engine ->
Execution Bridge), and [[project_v3_crypto_architecture]]'s own history
of retest-timing bugs (12-day-stale retest, cold-start seeding,
absolute recency check, sticky formed_time_confirmed) -- all of which
this webhook approach could make structurally unnecessary for
Reversal Manager's retest signal specifically, since Pine's own
push-based timestamp can't suffer the same polling/visibility-churn
artifacts tv_scraper's pull approach does.
