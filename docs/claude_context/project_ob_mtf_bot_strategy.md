---
name: project-ob-mtf-bot-strategy
description: "RETIRED - candle-based OB MTF zone-reaction reversal bot for XAUUSD, fully deleted from the repo (committed 6a1d7c5), kept only for historical design rationale. A brand-new SMC bot build is starting from a clean slate."
metadata: 
  node_type: memory
  type: project
  originSessionId: d3eb867f-7c44-41c8-a89e-659f140c8e60
  modified: 2026-07-24T11:42:28.665Z
---

**RETIRED as of 2026-07-24, teardown committed 2026-07-24 (commit 6a1d7c5 "Remove old OB/SMC reversal bot system entirely").** Every file from the old system is now gone from the repo, not just the working tree: all of `ob_mtf_bot/` (`__init__.py`, `bridge_reader.py`, `config.py`, `connection.py`, `execution.py`, `reversal_cap.py`, `reversal_signals.py`, `reversal_trader.py`, `risk.py`, `state_store.py`, `structure_watch.py`, `zone_targets.py`, `zone_watcher.py`) and all of `mql5_utils/` (`OB_Bridge_Aggregator.mq5`, `OB_StatePublisher_Indicator_v1.09.mq5`). `mt5_ma_bot/` (a separate, unrelated MA-based bot) was left untouched. This was a deliberate full clean-slate decision by the user ("I want to be deleted everything, will start brand new") - not an accidental deletion. The commit is local only, not yet pushed to `origin/main` as of this writing. This memory is kept only for historical design rationale (bias/reversal reasoning, cap mechanics, the H4/H2/H1-vs-M30/M15/M5 wick-rejection split) in case it's useful context for the new build - do not assume any of this is live or actionable, and do not reuse any of this code, without the user explicitly asking to revive a specific piece of it.

**Next step**: user intends to design and build a brand-new SMC (order block + FVG) bot from scratch. No new strategy rules have been discussed yet as of this memory's writing - when that conversation starts, follow the discuss-first, one-question-at-a-time process in [[feedback-algo-trading-collab]] rather than assuming any carryover from the old design.

Building a Smart Money Concepts (order block + FVG) automated trading system for XAUUSD in `C:\Users\ARK\projects\Algo`, using MT5 terminal `MetaTrader5-5` (account "Algo Development" 433882776, demo/trial server Exness-MT5Trial7). **The bot is live and actively trading** as of 2026-07-23 - this is not a design exercise anymore, it places real orders on the demo account continuously.

**The old pre-existing `ob_mtf_bot` engine has been fully removed** (deleted `main.py`, `setups.py`, `tracker.py`, `ob_detection.py`, `backtest.py`, the old MQL5 EA). `execution.py`/`config.py` trimmed to only what the new system uses. None of `ob_mtf_bot/` was ever committed to git before this rewrite, so nothing was lost. Do not reference or try to reuse anything from the old engine - it no longer exists.

**Architecture (all built, committed, pushed to `origin/main` on GitHub `surajryder-sys/Algo`):**
- `mql5_utils/OB_Bridge_Aggregator.mq5` - single MT5 indicator, walks every open chart for the symbol, reads OB rectangles ("pineBox" objects from "Order Block Detector", closed-source/compiled only) and FVG rectangles (from `FVG_Retest_V2.mq5`, user-authored, has retest-circle objects for accurate historical retest status) across H4/H2/H1/M30/M15/M5, recomputes "Dynamic Zones" (from `Dynamic Zones.mq5`, also user-authored) from D1 bars, writes one JSON snapshot to MT5's Common\Files folder every 2s. FVGs capped to 6/side/timeframe on the bridge side.
- `ob_mtf_bot/bridge_reader.py` - Python read side of that JSON.
- `ob_mtf_bot/structure_watch.py` - standalone cross-timeframe structure report (confluence detection, not part of the live trading path).
- `ob_mtf_bot/reversal_signals.py` + `ob_mtf_bot/zone_watcher.py` - stateful per-zone touch tracking: starts watching on touch, fires at most one signal per touch episode, re-arms after price departs and returns. Detection uses fixed **M1** confirmation candles for every zone regardless of the zone's own timeframe. `ReversalSignal` carries a stable `identity` (OB signature / FVG name).
- `ob_mtf_bot/zone_targets.py` - second-nearest same-direction zone (unified OB+FVG ranking) for trailing SL; zone-broken detection.
- `ob_mtf_bot/reversal_cap.py` - both whipsaw brakes, JSON-persisted: single-zone cap (4 fires without breaking) and two-zone ping-pong (3 round trips / 6 alternating events without either breaking). Unit-tested, all edge cases pass.
- `ob_mtf_bot/reversal_trader.py` - the live execution loop. Run with `python -m ob_mtf_bot.reversal_trader`. Distinct magic number `OB_MAGIC_NUMBER + 1000` (26072502). Logs to `reversal_trader_log.txt` and (as of the crash-resilience fix) `reversal_trader_crash.log`.

**Detection logic - the H4/H2/H1 vs M30/M15/M5 split (added 2026-07-23, commit f44e984):**
- **H4, H2, H1 zones**: no close-through requirement at all - a candle just needs a wick that reaches into the zone with a dominant wick-to-body ratio (≥1.5×) to count as `wick_rejection`. Added because these zones are wide (tens of points) and their own candle takes hours to close; the M1 confirmation candle would otherwise need to travel the zone's full width to ever fire, missing real rejections (a live example: an inverted hammer on M15 rejecting an H4 FVG was being missed entirely under the old close-through rule). Applies "be it inside the zone, or closed out of the zone, or wicked in and closed down" - close position is irrelevant, only the wick ratio matters.
- **M30, M15, M5 zones**: unchanged, still require the candle to close back outside the zone (`rejection_close`), or the same wick-ratio check as a stronger variant (`wick_rejection`).
- `engulfing` (body engulfs prior candle's body while touching the zone) is unaffected by this split, applies the same way to all timeframes.

**Crash resilience (added same commit, root-caused a real ~17hr outage):** a `PermissionError` from reading the bridge JSON mid-write by the MQL5 aggregator (transient file lock, writes every 2s) was uncaught and crashed the whole process on 2026-07-22, leaving an open LONG position unmanaged for ~17 hours until its SL was hit passively for **-50.62**. Fixed: broadened the catch to `OSError`, added a catch-all `except Exception` around the trading cycle (logs full traceback via `log.exception`, keeps the loop alive), and logging now also writes to `reversal_trader_crash.log` so a traceback survives even if the console window is closed.

**Strategy rules decided and live:**
- Entry: any confirmed signal (rejection_close / wick_rejection / engulfing) at any OB or FVG zone, any of 6 timeframes, watched independently - no HTF bias pre-filter, no OB+FVG confluence requirement.
- SL: zone edge ± buffer (`OB_SL_BUFFER`=0.50), floored at `OB_MIN_SL_DISTANCE`=7.00 from entry via `risk.py`'s `apply_minimum_sl` (pre-existing, reused as-is). Always based on the *triggering* zone's own boundary, regardless of which timeframe it's on.
- Reversal: opposing signal while in a trade closes and flips using that signal, from ANY timeframe (even lower TF against a higher-TF-based position) - UNLESS the zone is capped or paused, in which case only a STRONG signal (wick_rejection/engulfing) closes (no flip), plain rejection_close is ignored.
- **No TP is ever set anywhere** - positions managed purely by trailing SL + signal-based auto-exit, whichever fires first. `place_market_order` always sends `tp=0.0`.
- Trailing: once in a trade, trails to the **second-nearest** same-direction zone (unified OB+FVG ranking across all timeframes, skip the nearest deliberately for retest room), ratchets forward only.
- Single-zone cap: 4 fires without breaking → an existing position there only closes (no flip) on a strong signal, weak signals ignored. New entries currently unaffected (see open item below).
- Two-zone ping-pong: 3 round trips (6 alternating reversals) between the same two zones → both pause, no new trades from either until one breaks.
- **Position sizing: fixed 0.03 lots** (`OB_LOTS` in `.env`, raised from the original 0.01 once the user decided to go live) - not risk-based, deferred until backtest/live-test data exists.

**Explicitly still open / not yet decided (do not assume answers, ask):**
- **Bias/priority/tie-break layer - DESIGNED IN DETAIL BUT NOT YET BUILT.** This is the next planned piece: (1) per-timeframe bias tracker, updated whenever `zone_watcher` fires a signal on that timeframe; (2) priority = count of timeframes agreeing per direction, with virgin-zone rejections weighted higher than already-tested-zone rejections; (3) entry gate - a new signal only fires if its direction has more timeframe-priority than the opposite; (4) tie-break when priority is equal both ways - use the single most recent directional event across all timeframes (either a brand-new zone forming, or a rejection firing at an existing older zone, whichever is more recent), and that zone becomes the SL reference. Motivated by a real bad trade: a short fired off a marginal H4 FVG touch while H2/H1/M15/M5 were all simultaneously bullish-biased - the priority gate would have blocked it. Two open sub-questions never resolved: exact tie-break behavior details, and whether/how this interacts with the existing per-zone cap/pause mechanics.
- What "capped zone" should mean for *new* entries now that TP/scalp-target no longer applies - deferred until backtest/live-test data exists.
- Account-level loss limits (daily loss cap etc.) - not addressed.
- Session/news filtering - not addressed.
- Bridge-staleness check before trusting a snapshot for a live decision - not addressed (though the crash-resilience fix indirectly helps, since a stale/locked read now just retries instead of crashing).

**Monitoring**: a session-only cron job (20-minute interval, checks process/position/balance/log for anomalies) was set up via `/loop` - **this does NOT persist across sessions**, it dies when the Claude session ends and must be re-created in any new conversation if ongoing monitoring is wanted. `/schedule` (cloud-based) is the durable alternative if that's ever wanted instead.

**Scope**: XAUUSD only. Multi-symbol/crypto extension is an explicit future goal, deliberately deferred - do not generalize past XAUUSD unless asked again.

**Why**: user designs this collaboratively, wants every strategic rule explicitly discussed and confirmed before it's coded into order-placing logic. It now trades real orders continuously (demo account, but same code path would trade live).

**How to apply**: before writing/extending trading execution code, check the "still open" list and don't assume an answer - ask. The bias/priority/tie-break layer is the clear next build item if the user wants to continue that thread. Check current MT5 position/log state before assuming anything about "current" bot behavior, since this is a live, continuously-changing system, not a static codebase.
