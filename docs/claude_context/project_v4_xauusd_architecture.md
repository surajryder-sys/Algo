---
name: project-v4-xauusd-architecture
description: "V4 is a new bot lineage rebuilding XAUUSD entry logic from scratch, starting with a Trend Manager; V3 stays running untouched alongside it"
metadata: 
  node_type: memory
  type: project
  originSessionId: b1bfb561-670a-4277-b383-7a5fdd4527e2
  modified: 2026-08-27T18:34:39.097Z
---

Started 2026-08-26. User's complaint about existing XAUUSD logic (algo_v2
MT5-native, and v3/algo_v2_tv_xauusd TradingView-native): too many false
trades, wrong directions, missed good trades. Decision: keep V3 running
as-is, build a brand new lineage "V4" starting from XAUUSD only, other
symbols (USTEC/USOIL/BTC/ETH) later. Code lives in `v4/`. Two separate
components planned, built one at a time: **Trend Manager** (trade with
prevailing direction -- in progress) first, then **Reversal Manager**
(trade zone reversals) after -- same split as the v3 crypto lineage, see
[[project_v3_crypto_architecture]].

## Data sources (two, deliberately separate, not merged)

1. **TradingView scraper** (`v3/tv_scraper`, existing infra, reused as-is)
   -- still the source for the full 8-timeframe OB zone picture
   (H4/H2/H1/M30/M15/M5/M3/M1) used for Reversal Manager's "reversal
   zones"/"buffers" concept (not yet built). Its ATR-trend bridge had a
   real bug fixed 2026-08-26/27: `parser.py`'s `_ATR_LABELS` looked for a
   dead `"Trailing Stop"` Data Window label -- broke silently when
   `pine/OBD_ATR.pine` was consolidated to two lines
   (`"Line 1/2 Trailing Stop"`) on 2026-08-20, freezing every TF's
   committed trend since that date. Fixed in `parser.py` +
   `atr_trend_tracker.py` (now tracks each line's trend independently via
   `update_line`, combines via `update_structure`: both lines agree ->
   STRONG/WEAK, disagree -> UNDECISIVE) + `scraper.py`. A legacy
   single-value key (mirroring line1/fast) is still written so
   `v3/algo_v2_tv_xauusd`'s existing reader doesn't break.

2. **New MT5-native bridges**, scoped to only 3 charts -- M5, M3, M1 --
   each attached individually (not the old one-instance-scans-many-charts
   pattern), feeding V4's execution engine:
   - `mql5/SurajBot_ATRTrail_FINAL_LIVEFIXED_REALTIME_DUAL.mq5`
     ("ATR Trail Dual" on-chart) -- publishes
     `ATRSTATE_DUAL_<symbol>_<tf_minutes>.json` (Common Files\OBBridge):
     both trail lines' trend/event_time plus combined
     STRONG/WEAK/UNDECISIVE `structure` (same rule as the TV-scraper
     fix, kept identical on purpose). Real historical event_times
     (backward bar-scan), not restart artifacts.
   - `mql5/OB_Zone_Bridge_Lite.mq5` ("OB Zone Bridge Lite") -- publishes
     `OBSTATE_LITE_<symbol>_<tf_minutes>.json`: bull/bear zone history
     (top/btm/virgin/start_time/detected_time/detected_price) + `bias`
     (direction of the single most-recent zone, either side). Deliberately
     trimmed vs the old full publisher: no on-chart panel/labels/buttons/
     GlobalVariables, and explicitly no `visit_time`/`validation_time` in
     the published JSON (user wants MT5 side as light as possible -- see
     [[feedback_mt5_indicator_lightweight]]) even though the retest
     reconstruction happens internally to compute `virgin` correctly.
   - Old 8-timeframe `OBSTATE_XAUUSD_*.json` (from
     `OB_StatePublisher_Indicator_v2.00.mq5`, algo_v2's own bridge) were
     found frozen ~31h stale (M5 completely empty) and deleted 2026-08-27
     -- confirmed safe since algo_v2's Python process (`python -m
     algo_v2.main`) was NOT actually running despite
     `SMC_V2_ENABLE_TRADING=true` in its `.env`. That old publisher's own
     staleness/M5-empty-zones root cause was never diagnosed (no
     `OB_StatePublisher_Indicator` log lines at all in 2 days of MT5
     terminal logs) -- if algo_v2 is ever restarted, check this bridge is
     actually alive again first.
   - `v4/bridge/reader.py` -- Python reader for both new bridges
     (`ATRDualSnapshot`/`OBLiteSnapshot`/`TFSnapshot`, `read_all(symbol)`
     returns one bundled snapshot per M5/M3/M1), tested working against
     live files. Reuses `ob_bridge.reader.bridge_root()` only; schemas
     differ too much from `ob_bridge`/`atr_bridge`'s own dataclasses to
     reuse those directly.

MT5 environment notes: terminal ID
`4DB333B24A74B726D7AA441A9D0137DC`, install path
`C:\Program Files\MetaTrader5-5`. MetaEditor CLI compile works headless
(`MetaEditor64.exe /compile:"<path>" /log`, check
`logs\metaeditor.log` for the real result -- exit code is unreliable,
often 1 even on a clean compile). Attaching an indicator to a chart, or
getting an already-attached instance to pick up a recompiled `.ex5`,
still requires the user's own manual action in the terminal (no GUI
automation) -- toggling the indicator off/on or removing+re-adding it
forces the reload; a bare recompile does not hot-swap a running instance.

## Trend Manager logic (design agreed, not yet built)

Timeframe tiers: HTF = H4/H2/H1, short-term = M30/M15/M5, LTF = M3
(analysis) + M1 (execution). Bias-setting rule for M3: three candidates
race -- M3's own latest OB direction/time, ATR line1's flip time, ATR
line2's flip time (all on M3) -- whichever is most recent wins and sets
direction. M1 then needs a confirmation after a **pullback** (exact
definition still open): either an ATR flip on M1 or a fresh OB on M1 in
the bias direction, whichever fires first, then executes via the
existing M1 OB-entry mechanics (unchanged from algo_v2's approach).
UNDECISIVE (one ATR line flipped, not both) is explicitly its own valid
lower-conviction entry type in some of "the logics," not just "no trade"
-- still open whether single-line entries are valid anywhere beyond
M3/M1, and the precise pullback definition, before this can actually be
coded.

## Reversal Manager concepts (discussed, deferred, not yet built)

Every untested/virgin zone on any TF = a "reversal zone" (potential
reversal point, not an automatic entry). "Buffers": an active bull
(demand) zone blocks new shorts, an active bear (supply) zone blocks new
longs -- buffers built only from H4/H2/H1/M30/M15/M5 zones, never
M3/M1. Still open: whether a buffer is proximity-based (only while price
sits inside that zone's range) or standing/global (blocks everywhere
until mitigated) -- unanswered, blocks building this component.
