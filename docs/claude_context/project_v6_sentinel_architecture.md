---
name: project_v6_sentinel_architecture
description: "V6-Sentinel build decisions as they're made (multi-instrument scope, component ports) -- companion to project_v5_sentinel_xauusd which is the reference map being ported from"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1a28a4fc-a986-47a1-8ca7-10339f148773
  modified: 2026-09-19T16:03:36.144Z
---

**V6-Sentinel** ("V6S") build started 2026-09-18, no code yet as of this entry (no `v6_sentinel/` folder or branch exists). Reference map for what's being ported from: [[project_v5_sentinel_xauusd]]. Working style: component-by-component, verify-then-copy, discussed per component -- see [[feedback_algo_trading_collab]].

## Confirmed scope decisions

- **Multi-instrument from day one, XAUUSD-only initially.** Unlike V5-Sentinel (built XAUUSD-only, XAUUSD-scaled thresholds hardcoded throughout), V6-Sentinel is meant to eventually run multiple instruments -- but the first working build targets XAUUSD only, with other symbols added later.
- **Architecture pattern for this: symbol-agnostic instance** (confirmed 2026-09-18, user picked this explicitly over two alternatives). Each Manager/component takes symbol as a parameter/config from the start -- one instance can serve any symbol; magic numbers, SL thresholds, state file paths etc. are keyed by symbol rather than hardcoded. Matches the proven pattern from [[project_v3_crypto_architecture]] (V3's crypto Trend/Reversal Managers: one symbol-agnostic instance handling all 3 symbols, not one instance per symbol) -- explicitly rejected both a generic per-cycle symbol loop (like `algo_v2_fx`) and a "build XAUUSD-concrete now, generalize later" approach.
- **How to apply**: every V6S component port must take this into account from its first line of code -- config dicts/objects keyed by symbol, no bare hardcoded symbol strings or symbol-scaled constants inline. Adding a second instrument later should mean adding a config entry, not touching component logic.

## Confirmed: zone data source (2026-09-18)

V6S's NLB/NSB Block reads V5S's EXISTING tv_scraper output file
(`v5s_tv_scraper_zones.json`) read-only, rather than running a second
scraper instance. **Why**: avoids a second browser window and the
already-documented maximized-window/CDP-conflict risk (see
[[project_tv_scraper_window_maximized]]); V6S accepts a runtime
dependency on V5S's tv_scraper process staying alive as the trade-off.
**How to apply**: V6S's own nlb_nsb_watcher (not yet built) points its
zone_state_file config at the SAME file V5S's tv_scraper already writes
-- never write to that file from V6S, read-only. If V5S's tv_scraper is
ever stopped/retired, V6S's Block goes stale silently until this is
revisited.

## Progress (2026-09-18)

Building on the current branch `add-ob-state-publisher-2` (user's explicit choice -- not a new branch), uncommitted so far. `v6_sentinel/` created with the first component: the core bridge/data chain, verified file-by-file against the real v5_sentinel/ source before porting (not copied blind) --
`config.py` (new, minimal -- only ACTIVE_SYMBOLS list + `state_file_for(component, symbol)` helper; magic numbers/lots/SL thresholds deliberately left out, they belong to TM/RM components not yet ported), `rates.py`, `bridge.py`, `st_bridge.py`, `flip_state.py` (all four ported verbatim -- confirmed fully stateless/pure and already symbol-parametrized, no multi-instrument change needed), `bridge_flip.py` and `bridge_bar_flip.py` (ported with a documented caveat instead of an internal rewrite: `StaleAlertTracker`/`BridgeBarFlipTracker` are keyed by `tf_minutes` only, same as V5S -- safe under the symbol-agnostic-instance pattern ONLY because the orchestration layer must create one instance per symbol, each with its own state file via `state_file_for()`; this is noted in both files' docstrings so it isn't silently violated later), `structure.py` (ported verbatim, arbitrates Supertrend vs ATR-structure per timeframe). All 9 files verified to compile and import cleanly (`py_compile` + a live `import` test, `python -c` from repo root) -- no MT5 terminal connection available to test live data flow yet.

**Not yet built**: any orchestration/main-loop file that actually instantiates per-symbol trackers (nothing runs yet -- this is data/bridge primitives only), htf_levels.py/nlb_nsb_block.py/ict_ob_block.py (not yet verified for the same per-symbol-keying gap -- nlb_nsb_block.py's `BlockStore` looked already closer to symbol-safe on a quick read, zones carry their own `symbol` field and pruning is symbol-scoped, but not fully confirmed), the tv_scraper sub-package, and every entry/execution component (TM-STR, RM-STR, RM-ICT, TM-ICT, SL managers, exit manager, etc.) -- all still to be discussed and ported one at a time.

## RM-STR + RM-ICT port (2026-09-18, session 2)

User asked to build RM-STR + RM-ICT next. Full dependency graph mapped
first (bigger than just the entry-logic files -- both components
transitively need the Block/zone layer: `ict_guard.py`'s proximity check
is applied to RM-STR's OWN entries only, never RM-ICT's, since RM-ICT
already trades directly off those zones -- confirmed by reading
reversal_main.py directly, not assumed from memory).

**Confirmed decisions this session**:
- Zone data source: V6S reads V5S's EXISTING tv_scraper output file
  read-only (see the dedicated section above) -- both RM-STR (via
  ict_guard) and RM-ICT (directly) depend on this.
- Magic numbers: V6S's RM-STR/RM-ICT use **26091801/26091802** --
  deliberately DIFFERENT from V5S's own 26090701/26090702, since V5S is
  still running live on the same MT5 account and sharing a magic number
  would let V6S's position-management code see V5S's real positions as
  its own. Any future V6S component MUST get its own fresh magic number
  too, never reuse a V5S one, for as long as both lineages coexist.

**Ported this session** (all verified against real V5S source, not
copied blind; all compile + import clean):
`decision_log.py`, `heartbeat.py` (trivial utilities, path-parametrized
already), `cisd_bridge.py` (CISD triggers M3CD/M5CD, stateless), `broker.py`
(MT5 order/position plumbing -- one deliberate API change: `connect()`
now takes explicit credential args instead of a whole `Config` object,
since V6S has no single per-process Config), `ob_levels.py` (**found and
fixed a real hardcoded-symbol bug**: `read_all_zones()` hardcoded
`"XAUUSD"` in its zone-store lookup key even though its own sibling
`nlb_nsb_block.py` already took `symbol` as a parameter -- both functions
now take `symbol` explicitly), `nlb_nsb_block.py` (`BlockStore` -- was
already fully symbol-scoped throughout, ported verbatim), `ict_guard.py`
(symbol-agnostic already, no change), `sl_manager.py`/`trade_manager.py`
(both keyed by MT5 ticket, not symbol -- no multi-instrument change
needed at all), `reversal_config.py` (reshaped for V6S: one
`RMSymbolConfig` per symbol via `_SYMBOL_DEFAULTS` dict + per-symbol env
overrides, raises clearly if a symbol has no tuned entry rather than
silently reusing XAUUSD's thresholds -- mirrors how V3's crypto lineage
required deliberate tuning per symbol), `reversal_entry.py` (RM-STR
signals) and `reversal_ict.py` (RM-ICT signals + `ICTEligibilityStore`)
-- both fully symbol-parametrized already, zero logic changes needed,
ported verbatim with V5S->V6S renaming only.

**Bug caught mid-port**: `bridge_flip.py` was initially ported
incomplete -- a two-part `Read` call (offset 37 + limit 45) undershot
the real 88-line file by 6 lines and silently dropped `m3_far_line()`,
only caught because `reversal_main.py`'s own import line referenced it
and the names didn't match. Fixed by re-reading the missing tail and
verified every other fully-read source file's line count against `wc
-l` afterward to confirm no other file was truncated the same way.
Lesson: when a `Read` call uses offset/limit, cross-check total line
count against `wc -l` before trusting the read is complete, especially
when porting code that must be reproduced exactly.

**Completed this session**: `critical_alerts_telegram.py` (generic
Telegram client, verbatim), `reversal_main.py` (rebuilt around a
`_SymbolRuntime` dataclass -- one full bundle of every stateful
tracker/store per symbol, built by `_build_runtime(symbol)` and looped
over in `main()`/`run_once()`, instead of V5S's single flat `RMConfig`;
`broker.connect()` call site updated for the new explicit-args
signature), `nlb_nsb_watcher.py` (new for V6S -- loops
`config.ACTIVE_SYMBOLS`, one `BlockStore` per symbol, reads V5S's
`v5s_tv_scraper_zones.json` read-only per the confirmed zone-data
decision). **RM-STR + RM-ICT are now fully ported end-to-end** (24
modules total in `v6_sentinel/`, all verified to import cleanly via a
`pkgutil` sweep) -- nothing has been run against live MT5/broker data
yet, and `ENABLE_TRADING` is unset (defaults false) everywhere, so no
order can be sent. To actually run: `python -m v6_sentinel.nlb_nsb_watcher`
and `python -m v6_sentinel.reversal_main` (needs V5S's tv_scraper
process alive for zone data, and an MT5 terminal connection).

**Not yet ported / built for V6S at all**: TM-STR, TM-ICT (own separate
`ict_ob_block.py` zone store, not yet checked for the same per-symbol
gap), `exit_manager.py`, `critical_alerts_watcher.py`/`_listener.py`,
`watchdog.py`, the `tv_scraper` sub-package (deliberately not needed --
V6S reads V5S's scraper output instead), `htf_levels.py` was actually
already ported as part of this RM work (see above), so the "not yet
built" list from the previous session entry is now stale on that one
point.

## Structure signal now includes CISD (2026-09-18, session 3)

Confirmed with the user: `structure.compute_structure_signal()` --
shared by M15 Primary Structure (RM-STR/RM-ICT's own gate, already
live) and M5 Parent bias (TM-STR, not yet built) -- now arbitrates
THREE sources by recency instead of two: Supertrend, ATR-dual-flip
(unchanged, trap-persists until a genuine FLIP/TRAP_RESOLVED per
existing `flip_state.py` semantics -- this was already correct, not a
new behavior), and CISD (bullish confirmation -> bullish structure,
bearish -> bearish). One shared rule for both M15 Primary Structure and
M5 Parent, not two special cases.

Design choices made while implementing (not fully specified in the
user's own one-line spec, flagged back to them, no objection so far):
- CISD reads the bridge's STANDING last-known confirmation
  (`cisd_bridge.read_cisd()`), not the momentary `fresh_cisd()` --
  matches how Supertrend's own `read_supertrend()` is already used here
  (current view of the source), as opposed to `fresh_flip()`/
  `fresh_cisd()`'s "did it confirm on the exact last bar" contract that
  reversal_entry.py's own M3CD/M5CD one-shot triggers use instead.
- Ties (identical event_time) favor SUPERTREND > ATR_STRUCTURE > CISD,
  extending the module's original ST-over-ATR tie preference.
- **CISD is excluded from arbitration entirely** (never becomes the
  deciding source, even if it's the most recent event) when its own SL
  basis (`cisd_bridge.sl_basis()` -- nearest active swing low/high) is
  unavailable -- `StructureSignal.sl_value` must always be a real,
  usable price for a future M5 Parent caller, so a CISD confirmation
  with nothing to anchor an SL to isn't a candidate that cycle; falls
  through to Supertrend-vs-ATR as before.

Verified against live M15 XAUUSD data. First attempt used a brand-new,
empty scratch BridgeBarFlipTracker file and got a WRONG answer (an
artifact "event at 01:30 IST" that was actually just the cold-start
bootstrap treating the current bar as if it had just confirmed, not a
real flip) -- caught and corrected by re-testing against a read-only
COPY of V5S's own real, continuously-accumulated
`v5s_reversal_manager_bridge_bar_flip_state.json` instead (V6S has never
run continuously so has no real history of its own yet). Real answer:
Supertrend flipped bearish 2026-09-18 00:45 IST, ATR FLIP to bearish and
CISD bearish confirmation both at 2026-09-17 23:45 IST -- Supertrend
would have won 3-way arbitration as the most recent.

**Lesson for future diagnostics**: never test compute_structure_signal()
or any BridgeBarFlipTracker-based read against a fresh/empty scratch
state file and trust the result -- it bootstraps instead of reflecting
real history. Copy V5S's own real state file (read-only, into a scratch
copy, never the original) for any one-off diagnostic that needs genuine
historical event times, until V6S has been running continuously long
enough to have real history of its own.

## Supertrend temporarily excluded from arbitration (2026-09-18, same session)

Confirmed with the user right after the above: "as of now we will use
ATR Dual Trail and CISD, and we see supertrend later." Implemented as:
`compute_structure_signal()` still reads Supertrend every call and keeps
it on `StructureSignal.supertrend` (now `Optional`) for diagnostics/
decision-log visibility, but it's removed from the arbitration candidate
list entirely and no longer gates the function's own None-return (ATR
dual-trail is now the sole required source; CISD stays optional as
before). `_SOURCE_PRIORITY` still reserves SUPERTREND's tie-break slot
for when it's reinstated -- re-adding it to the candidate list later is
a small, isolated change. Re-verified against the same real V5S state
copy: with Supertrend excluded, ATR_STRUCTURE now correctly wins
(2026-09-17 23:45 IST bearish, tied with CISD at the same timestamp,
ATR wins the tie per priority order) instead of Supertrend's later
00:45 IST event.

## RM-ICT full redesign -- DESIGN CAPTURED, BUILD PAUSED (2026-09-18, session 4)

User wants to focus on RM only for now (explicitly: don't suggest TM).
Described a complete replacement for RM-ICT's entry logic -- confirmed
"full replacement" of the old ST1F/M3F/ST3F/M5F + M15-Primary-Structure-
gated system (the one ported faithfully from V5S last session). Design,
for whenever the scraper catches up (see below):

**Reversal zones** = untested (virgin) OB zones only, across a NEW
timeframe scope: **H4, H2, H1, M30, M15, M5, M3** (7 timeframes) --
D1 dropped, H2 added (wasn't tracked before), M3 added (wasn't traded
before, though scraped). M1 zones are scraped too but NEVER traded for
reversals.

**Entry rule**: a zone becomes eligible the moment it's **touched**
(price re-enters its range -- this is exactly nlb_nsb_block.py's
existing `zone.retested` flag, no new touch-detection needed). Once
touched, wait for a **fresh** (not already-standing) CISD confirmation
in the MATCHING direction (bullish zone needs bullish CISD, bearish
needs bearish), sourced from a timeframe pool that depends on the
zone's OWN timeframe:
  - H4/H2/H1/M30/M15 zones -> confirmation from **M3 or M5** CISD,
    whichever fires first.
  - M3 zones -> confirmation from **M1 or M3's own** CISD, whichever
    fires first.
  - M5 zones -> confirmation from **M1, M3, or M5's own** CISD,
    whichever fires first.

**SEQUENCING is the critical rule, explicit from the user**: "if m3 is
already in bullish, we dont do anything, we wait an event to occur post
the touch, not before the touch... everything will be here in sequence
wise, recency matters." A CISD that's already sitting in the matching
direction AT touch-time does NOT count -- it needs a genuinely fresh
confirmation event that happens AFTER the touch.

**Implementation approach worked out** (not yet built): this
"post-touch, not pre-existing" requirement is naturally satisfied by
using `cisd_bridge.fresh_cisd()` (the "privileged, momentary, only
non-None the EXACT bar it confirmed" contract already used elsewhere in
this project for one-shot triggers) rather than `read_cisd()` (standing
state) for the confirmation check -- checked every cycle once a zone is
already eligible (touched + untraded). A standing-but-stale CISD from
before the touch would have already been "fresh" on ITS OWN bar (long
past), so by the time the zone becomes eligible and this check starts
running, only a confirmation that fires on some LATER cycle can ever be
caught -- no explicit touch-timestamp-vs-CISD-bar-time comparison
needed (which would have been awkward anyway: `zone.retested_at` is
wall-clock, `cisd.last_cisd_time` is bar-time, different clock domains).

**SL**: no longer CISD's own swing-basis -- now simply the zone's own
edge (bullish -> zone.btm/"ob low", bearish -> zone.top/"ob high") plus
a buffer. Simpler than before.

**Dependency simplification**: this new RM-ICT no longer needs M15
Primary Structure, `structure.py`, or `BridgeBarFlipTracker` at all --
purely OB-zone-touch + CISD-confirmation driven. (RM-STR is unaffected
by any of this and keeps its own M15-gated design + ICT Guard
safeguard, both untouched.)

**Verified before pausing**: checked the REAL scraper zone file
(`v5s_tv_scraper_zones.json`) against the described scope change --
**it does NOT reflect it yet**: D1 zones are still present, H2 zones
don't exist at all. M3's own grid-alignment offset (needed for
nlb_nsb_block.py's existing misattribution defense,
`_is_aligned_to_timeframe()`) WAS derivable from the 8 real M3 zones
already in the file: **remainder=0 for all 8** (same as M30/M15/M5/H1,
unlike H4's +3600s quirk) -- small sample but consistent, ready to add
whenever this gets built. H2's own offset can't be derived at all yet
(zero real zones) -- would need the same "leave unvalidated, like D1"
treatment until real H2 data exists.

**User's explicit decision when asked**: WAIT until the scraper is
actually updated (D1 removed, H2 zones actually appearing) before
building this -- do not implement against the target scope while the
live data still doesn't match it.

## RM-ICT redesign BUILT (2026-09-18, same session, resumed same day)

User confirmed the scraper had been updated ("i have changed ir"). Verified
before building (not just taking their word): re-checked
`v5s_tv_scraper_zones.json` -- H2 zones now existed (8 real ones), but
D1 zones were STILL present (scraper stopped polling that pane but
never pruned its stale old entries, since pruning only fires on
"missing from a pane it's still checking", not "pane no longer
checked"). User then explicitly said "delete d1 zones" -- removed the
two D1 keys (`XAUUSD|1D|bull`/`bear`) directly from the live scraper
JSON file (a gitignored runtime cache, not code -- low risk, scraper
just regenerates fresh data going forward).

Derived real grid-alignment offsets from the now-available data before
wiring them in (same methodology as H4's own original derivation):
**H2 (120): remainder=3600 for all 8 real zones** (same session-boundary
quirk as H4 -- both are >H1 timeframes), **M3 (3): remainder=0 for all 8**
(same as every other <=H1 timeframe). Both added to
`nlb_nsb_block._TIMEFRAME_SECONDS`/`_TIMEFRAME_OFFSET`.

**Files changed**:
- `ob_levels.py`: `TIMEFRAMES` now `("240","120","60","30","15","5","3")`
  -- H4/H2/H1/M30/M15/M5/M3, D1 dropped, M1 deliberately excluded (never
  a reversal-zone source, only a CISD confirmation source).
- `nlb_nsb_block.py`: alignment tables extended (H2/M3 added, D1 entry
  no longer relevant), SCOPE docstring updated to 7 timeframes.
- `reversal_ict.py`: **fully rewritten**. Dropped `structure`/
  `st_bridge`/`BridgeBarFlipTracker`/`EventType` imports entirely -- no
  longer needed. New `_ZONE_CISD_POOLS` dict maps each zone timeframe to
  its own CISD confirmation pool (H4/H2/H1/M30/M15 -> M3-or-M5; M3's own
  exception -> M1-or-M3; M5's own exception -> M1-or-M3-or-M5). New
  `_check_zone()` helper checks `cisd_bridge.fresh_cisd()` (momentary,
  not `read_cisd()`'s standing state -- this is what makes "must be
  post-touch, not pre-existing" work with zero extra timestamp
  bookkeeping) against each pool timeframe, matching direction. SL is
  now `zone.btm`/`zone.top` +/- buffer, no longer CISD's own swing-basis.
  `ICTEligibilityStore` (is_traded/overlaps_traded) carried over
  unchanged -- still the right protection against re-trading a churned
  zone identity. New signature: `find_ict_signals(symbol,
  block_state_file, eligibility, sl_buffer)` -- dropped the `tracker`
  parameter entirely.
- `reversal_main.py`: updated the one call site to match the new
  signature (no more `rt.tracker` passed to RM-ICT).

**Verified end-to-end** against the real (now-cleaned) scraper file via
scratch BlockStore/ICTEligibilityStore copies (never touching real V6S
state, which doesn't exist yet since nothing's run live): 53 zones
seeded across exactly the 7 target timeframes, confirmed zero M1/D1
zones seeded, 22 already touched (retested), `find_ict_signals()` runs
clean with no errors (empty result this instant, expected -- no fresh
CISD happened to fire at the exact test moment). All 24 v6_sentinel
modules still import cleanly after the change.

**Update 2026-09-19**: RM-STR itself has SINCE been fully redesigned too
(see the dedicated section below) -- this note is now historical only.

## RM-STR full redesign (2026-09-19)

User: "big changes in RM STR". Same overall mechanism as RM-ICT's own
redesign (touch + post-touch CISD confirmation) but applied to HTF
lines instead of OB zones, and with real new engineering underneath it.

**New timeframe scope**: D1, H4, H2, H1, M30, M15, M10, M5 (8 TFs,
replacing the old 9: dropped H8/H6/H3, added M10 -- confirmed
`mt5.TIMEFRAME_M10` is a real standard constant before adding it).

**Native Supertrend computation added** (`rates.py`) -- confirmed with
the user: "we dont need charts open, as we are getting live rates from
meta trader," so Supertrend is now computed directly from
`copy_rates_from_pos`, same self-contained approach `read_atr_dual()`
already used, NOT read from the MQL5 indicator's bridge file anymore
for this purpose (`st_bridge.py` still exists/still used elsewhere,
e.g. M1/M3 fresh-flip triggers were fully removed from RM-STR too, see
below). Ported the exact formula from `mql5/Supertrend.mq5` (verified
against the REAL live indicator's own source,
`ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar.mq5`'s `ST_`-prefixed
inputs, confirmed identical defaults: ATR period 10, hl2 source,
multiplier 3.0, Wilder-smoothed ATR) -- reuses `_wilder_atr()` already
in the file.

**Real fidelity gap found and disclosed, not hidden**: cross-checked
the native M15 Supertrend against the live bridge's own real output --
same direction, values within ~0.6 points on XAUUSD, but flips ONE BAR
earlier natively (2026-09-17 23:45 vs 00:15 IST). Traced bar-by-bar:
a genuine, small residual difference between MT5's built-in `iATR()`
and this project's own `_wilder_atr()` reimplementation occasionally
tips a close-to-boundary flip decision by one bar. Confirmed NOT a
warm-up issue (tested bar_count up to 20,000, identical result every
time) -- it's a seeding/algorithm characteristic of Wilder's RMA run
from a different starting point than the live indicator's own
since-attachment history, same category of caveat this file's own
docstring already gives for Major/Minor. User explicitly accepted this
level of fidelity after being shown the evidence.

**HTF levels (`htf_levels.py`) extended**: each timeframe now carries
THREE levels (2 ATR dual-trail lines, unchanged, PLUS 1 native
Supertrend line, new) instead of two. ATR and Supertrend track
COMPLETELY INDEPENDENT characters/event-times per timeframe (confirmed
live: M15 currently shows ATR=TRAP simultaneously with
Supertrend=BULLISH) -- `LevelEligibilityStore` reworked from a flat
`tf_minutes`-keyed dict to a `"{tf_minutes}:{source}"`-keyed one, every
public method (`sync`/`is_traded`/`mark_traded`/`mark_touched`/
`is_touched`) now takes an explicit `source` ("ATR" or "SUPERTREND")
parameter. User's own words confirmed this is exactly the right
mechanism, already mostly built: "a line moves, it doesn't stay at a
place like ob zones, so if the value is same, we are not trading it, if
value is different, it become eligible, or when it flips it becomes
eligible, traps and resolves lines can become eligible" -- the existing
value-keyed `mark_touched`/`is_touched` self-invalidation already
delivered the "value changed -> eligible again" half for free; only the
FLIP/TRAP_RESOLVED reset half needed splitting into two independent
(ATR, Supertrend) entries per timeframe.

**`reversal_entry.py` fully rewritten**: dropped M15 Primary Structure/
`structure.py` dependency entirely (RM-STR no longer needs it at all).
New rule: a touched, untraded HTF level (any of the 24 -- 8 TFs x 3
lines) waits for a fresh `cisd_bridge.fresh_cisd()` confirmation from
EITHER M3 or M5 (one uniform pool for every timeframe here -- NO
per-timeframe exceptions the way RM-ICT's own zones needed, since the
user gave one single rule: "confirmation from M3, M5 CISD, whichever
confirms fast"). SL = the touched line's own value +/- buffer ("we
enter trade based on the trigger timeframe, and execute based on cisd
timeframe").

**`reversal_main.py` simplified**: removed `BridgeBarFlipTracker`/
`StaleAlertTracker`/the `bridge` module import and the old
`_check_stale()` function entirely -- NEITHER STR nor ICT depend on the
live ATR-dual bridge for entry logic any more (only `m3_far_line()`,
used for ONGOING post-breakeven SL trailing, still reads the bridge --
unrelated to entry, left untouched since the user's redesign was
entry-logic-scoped only). `_tag()` now includes source
(`"H4/ATR/M3CD"` / `"M15/ST/M5CD"` style). `_SymbolRuntime` no longer
carries `tracker`/`stale_tracker` fields.

**Verified end-to-end** against real live MT5 data (scratch eligibility
store): 8 timeframes x 3 levels = 24 total, ATR/Supertrend correctly
independent per timeframe, `find_signals()` runs clean (empty result
this instant, expected). Full package still imports clean.

**SL basis corrected same day**: initially defaulted to "touched line's
own value +/- buffer" without confirming -- user then explicitly asked
"instead can we use swing low high". Clarified which of two different
"swing" concepts in this codebase: CISD's own internally-tracked nearest
active swing (`cisd_bridge.sl_basis()`, frozen at confirmation -- the
ORIGINAL basis this project always used for CISD triggers, before
RM-ICT's own redesign switched to zone-edge instead) vs a fresh N-bar
lookback (`rates.recent_swing_low/high()`). User picked CISD's own
tracked swing. `reversal_entry._check_level()` updated: SL is now
`cisd_bridge.sl_basis(cisd)` +/- buffer, and a CISD confirmation with no
active swing to anchor to (`sl_basis()` returns None) simply produces no
signal that cycle -- consistent with every other trigger's "no
fallback, no guess" rule. Re-verified end-to-end, clean.

**SL rule refined again (2026-09-19, later same session) -- swing is now
only the FALLBACK, lines are preferred.** User: swing high/low "only
when the ATR Dual or Supertrend not available"; example given: bearish
CISD SELL while every ATR/Supertrend line is below price as support --
can't be a SELL stop, so use swing high. "If any one line is available
... place sl with the value of line available with buffer."
Confirmed details (asked, not assumed): candidate lines come from **M5
first, then M3 only if M5 has none usable** (NOT the touched
timeframe's lines, NOT all 8 timeframes); if several are usable, the
**FARTHEST from entry** wins (widest SL, explicit choice over nearest).
"Usable" = on the correct side of the LIVE entry price (ask for BUY, bid
for SELL): strictly above for SELL, strictly below for BUY. Any single
line (ATR1/ATR2/Supertrend) is enough. Order: M5 lines -> M3 lines ->
CISD swing (`cisd_bridge.sl_basis()`) -> none (that CISD produces no
signal). Buffer applied on top in every case.
Implementation: `reversal_entry._line_values()` (computes M5/M3
ATR-dual + native Supertrend straight from copy_rates, cached per
`find_signals()` call -- M3 lines are NOT in htf_levels' 8-timeframe
scope so they're computed on demand, only when a signal is about to
fire) + `_initial_sl_basis()`. `find_signals()` now takes `bid, ask`;
`ReversalSignal` gained `sl_source` ("M5/ATR2", "M3/ST", "SWING") which
`reversal_main` writes to the decision log. Verified: 7 synthetic
branch tests pass (incl. the user's exact example) + live M5/M3 lines
computed (live BUY fell through M5 -> M3 as designed).
**Known risk flagged to user, not capped**: no max SL-distance limit --
"farthest line" can put the stop far from entry when a slow ATR line is
the farthest usable one. Not yet committed as of this entry.

**RM-ICT wide-zone SL override (2026-09-19, same session)** -- user
confirmed the line-first SL rule above was RM-STR ONLY, then extended a
version of it to RM-ICT: "if zone size is bigger than 15 points, and if
sl is more than 15 points, check same M5 lines, whichever far... if not
available on m5, then m3, if not available in both then swing high/low
with buffer." Implemented as: RM-ICT keeps its default zone-edge SL
(zone.btm/top -/+ buffer) UNLESS BOTH (a) zone height > 15 AND (b) the
zone-edge SL is > 15 points from live entry (ask BUY / bid SELL) -- then
the SL comes from the same M5 -> M3 -> CISD-swing rule RM-STR uses.
Interpretations I made (user did NOT explicitly confirm these, flagged
in the reply): both conditions must hold (AND); "sl distance" is
measured AFTER buffer, from entry; "> 15" strict (a zone exactly 15 is
not wide); if the override triggers but nothing is usable (no M5/M3 line
on the correct side AND no active swing) the signal is SKIPPED rather
than falling back to the wide zone-edge SL.
**Real design tension flagged to user**: the override uses the FARTHEST
line, so the replacement SL can end up WIDER than the zone-edge SL it
was supposed to replace -- the rule is triggered by "SL too wide" but
picks the widest candidate; no distance cap. Not resolved.
Code: extracted the shared logic into NEW `v6_sentinel/sl_basis.py`
(`line_values`, `initial_sl_basis`, `SL_LINE_TIMEFRAMES=(5,3)`) so
RM-STR and RM-ICT can't drift apart; `reversal_entry.py` now imports it
(private copies removed). Threshold is per-symbol config
`ict_wide_zone_points` (XAUUSD 15.0, env override
`V6S_RM_XAUUSD_ICT_WIDE_ZONE_POINTS`) in `reversal_config.py`, not
hardcoded. `find_ict_signals()` signature is now `(symbol,
block_state_file, eligibility, sl_buffer, bid, ask, wide_zone_points)`;
`ICTSignal` gained `sl_source` ("ZONE"/"M5/ATR2"/"M3/ST"/"SWING") which
`reversal_main` logs. A `_announced_skips` set stops the "nothing
usable" skip message reprinting every poll for a whole bar. Verified: 8
synthetic branch tests (small zone, wide+far SL, wide+near SL, M5->M3,
swing, nothing usable, SELL, exactly-15 boundary) + live run of both
components, all clean; 25 modules import. NOT committed yet.

## ICT Guard disabled for RM (2026-09-18, same session)

Confirmed with the user: "we are not using any guard here as of now
[for reversal manager]... later we might add it." Same "keep the
plumbing, skip the active use" treatment as Supertrend in structure.py
-- `reversal_main.py`'s `_open_position()` no longer calls
`ict_guard.check()` at all (previously applied to STR's own entries
only, never ICT's). `sticky: ict_guard.ICTGuardStickyStore` is still
threaded through every function signature and still instantiated in
`_build_runtime()`, so reinstating the actual check later is a small,
isolated change -- just re-add the `if component == "STR": ...
ict_guard.check(...)` block that used to live at the top of
`_open_position()` (see git history / this memory's own earlier
entries for the exact code). Verified: compiles clean, full package
still imports.

**How to apply**: read this alongside [[project_v5_sentinel_xauusd]] before continuing any V6S work -- that memory has the full V5S architecture/bug history being ported from; this one tracks what's actually been decided/built for V6S itself, kept current as work progresses.

**RM-ICT override trigger CHANGED to SL-distance ONLY (2026-09-19, right
after the above)** -- user: "i want this to check for another sl only
when sl is getting bigger than 15 points". The zone-size > 15 condition
is DROPPED (my interpretation: the user's word "only" meant the SL is the
sole trigger; not re-confirmed). Now: override fires iff the zone-edge SL
(after buffer) is strictly > 15 points from live entry (ask BUY / bid
SELL), regardless of zone size -- so a SMALL zone whose entry ran away
from it can now trigger it, and a WIDE zone with entry near the far edge
does not. Replacement rule unchanged (farthest M5 line -> farthest M3
line -> CISD swing; nothing usable -> signal skipped). Config field
RENAMED `ict_wide_zone_points` -> `ict_sl_override_points` (env
`V6S_RM_XAUUSD_ICT_SL_OVERRIDE_POINTS`) since "wide zone" no longer
describes it. RM-STR untouched (still line-first always, swing fallback).
Verified: 9 synthetic cases pass (incl. small-zone-far-SL triggers,
wide-zone-near-SL doesn't, SL exactly 15 doesn't); 25 modules import.
**Still unresolved and re-flagged with a concrete number**: farthest-line
selection can make the replacement SL WIDER than the zone-edge SL that
triggered it (test: zone SL 17pts from entry -> replaced by an M5 line
22pts away). No cap, nearest-vs-farthest question unanswered. NOT
committed yet.

**RM-ICT override made RISK-REDUCING ONLY (2026-09-19, right after the
above)** -- resolves the "farthest line can make SL wider" tension.
User: "if the new sl is 22 points, dont take new sl of 22 points, then
apply same sl. it was to reduce risk we were searching for lines on M5
and m3, not to increase risk or to wide sl." Now: when the zone-edge SL
is > `ict_sl_override_points` (15) from entry, a replacement counts ONLY
if its FINAL SL (basis -/+ buffer) is strictly TIGHTER than the
zone-edge SL (equal is not tighter). Implemented as an optional
`must_beat_sl` + `sl_buffer` filter on `sl_basis.initial_sl_basis()`;
RM-STR calls it without that filter so its behaviour is UNCHANGED
(still farthest usable line, no comparison). Order still M5 -> M3 ->
swing, farthest FIRST among the qualifying (tighter) candidates.
If nothing qualifies, the zone-edge SL is kept and the trade STILL FIRES
-- this REVERSES my earlier "skip the signal when nothing is usable"
interpretation (the `_announced_skips` set and its message were
removed). Interpretation I made, not explicitly confirmed: among
lines that ARE tighter, I still pick the FARTHEST of them (widest that
still reduces risk), and a tighter replacement is not additionally
required to be under 15pts itself. The user's literal instruction only
said "don't take the 22, apply same sl"; I read the intent ("to reduce
risk") as also wanting a genuinely tighter line to be used when one
exists rather than discarding all lines just because the farthest was
too wide. Verified: 10 synthetic cases (incl. the exact 22pt case,
equal-not-tighter, SELL mirror, SL<=15 untouched) + RM-STR unchanged
check + live run; 25 modules import. NOT committed yet.

**RM-ICT override FINAL FORM = literal reject, NOT a re-search
(2026-09-19, superseding the "farthest of the tighter ones" reading
just above)** -- I offered the user the choice between that
intent-based reading and the literal one; they answered "yes do it"
(taken as: switch to the literal rule) plus commit/push/sync. Final
behaviour in `sl_basis.initial_sl_basis()`: pick the candidate by the
NORMAL rule (farthest usable line on M5, else M3, else CISD swing);
then, if `must_beat_sl` was passed and that pick's final SL (basis
-/+ buffer) is NOT strictly tighter than the zone-edge SL, return None
= keep the zone-edge SL. NO hunting for a nearer/tighter line on the
same timeframe, and NO falling through to M3 or swing after M5's pick is
rejected. The trade always still fires. So e.g. zone SL 4358, M5 lines
{4355, 4368, 4372} -> farthest is 4355 (wider) -> rejected -> keeps
4358 even though 4368/4372 would have been tighter. RM-STR unchanged.
Helper split: `_pick()` (normal chain) + `initial_sl_basis()` (adds the
check). Verified: 12 synthetic cases incl. the exact 22pt case, the
no-re-search and no-M3-fallthrough cases, SELL mirror, plus the RM-STR
unchanged check; 25 modules import.

## structure.py DELETED from V6S (2026-09-20)

User: "delete it, we dont need in v6s" -- after I explained (twice; the
first explanation was too jargon-heavy) that "Supertrend deferred in
structure.py" only meant a bias-opinion module nothing in RM calls any
more, and that Supertrend is fully LIVE in RM-STR as a touchable line
(a separate native calc in rates.py) -- the two are unrelated. This
SUPERSEDES every earlier note in this file about structure.py, the
ATR+CISD+Supertrend arbitration, "Supertrend deferred", and the
M5-Parent/M15-Primary-Structure idea: that whole concept is gone from
V6S (recoverable from git history, last present in commit e0a6cb1).
Deleted three files together: `structure.py` plus `st_bridge.py` and
`bridge_bar_flip.py`, which existed ONLY to feed it (I chose to include
the two helpers because they were dead the moment structure.py went;
user said "delete it" -- flag if they wanted the helpers kept).
Also removed the now-dangling `bridge_flip`-unrelated config field
`bridge_bar_flip_state_file` from `RMSymbolConfig`, and rewrote stale
docstring references (bridge.py, bridge_flip.py, cisd_bridge.py,
config.py, htf_levels.py, reversal_*.py). `cisd_bridge.py`'s header was
also stale (still described ST1F triggers / "M3 and M5 only") and now
describes the real current use (RM-STR M3|M5; RM-ICT per-zone pools
incl. M1; swing = last-resort SL basis).
V6S is now 22 modules. Still-unused-but-kept: `bridge_flip.
StaleAlertTracker` (nothing imports it; only `m3_far_line` is used, for
post-breakeven SL trailing) and `ict_guard` (wired, check disabled).
Verified: all 22 import, deleted modules gone, ICT SL logic identical on
4 key cases, live cycle of both engines clean (8/8 HTF states, 55 zones
seeded; the scraper-misattribution alignment defense live-rejected a
bogus 4710-4718 "M15" zone). NOT committed yet.

## FIRST DRY-RUN (2026-09-20, Sunday 01:03 IST -- MARKET CLOSED)

User: "yes dry run". Verified BEFORE starting: `enable_trading` resolves
False (no V6S_* in .env or shell), no leftover v6s state files. Ran
`nlb_nsb_watcher` (130s cap) + `reversal_main` (50s cap) concurrently
against live MT5 -- read-only/decision-only, no order path reachable.
**LIMITATION -- weekend**: last XAUUSD tick was Sat 02:27 IST (price
frozen 4378.147/4378.237) and the CISD bridge files were ~7h stale (the
MQL5 indicators only republish on ticks), so this proved WIRING ONLY:
it could NOT produce or test a real signal, a live touch, or a live
invalidation. **The signal path (fresh CISD -> entry -> SL logic in a real
cycle) is still UNTESTED end-to-end and needs a re-run when the market
reopens (Monday IST morning).**
Results: both processes ran with ZERO cycle errors. Watcher seeded 55
zones (H4/H2/H1/M30/M15 x8, M5 x7, M3 x8; no M1/D1), 22 already marked
touched by the scraper ("seed" source), 0 live touches/invalidations
(frozen price). reversal_main computed 8/8 HTF states and created 16
(TF x ATR/Supertrend) eligibility entries, each announced once.
No ICT-eligibility/decision-log/SL/TM state files were created (correct:
nothing traded, no positions) -- so NO "traded" flags polluted state
(a decision-only signal WOULD mark a level traded even with no order
sent; worth remembering: delete v6s_* state files before going live if a
market-hours dry-run ever produces decision-only signals).
**Real defect found + fixed**: the watcher printed the same rejected
zone (a bogus scraper "M15" zone 4710-4718, ~330pts from price, with a
non-candle-aligned start_time) EVERY cycle -- 94 identical lines in ~95s
(~86k/day), the same repeat-every-cycle spam V5S had, carried over in
the port. Fix in `nlb_nsb_block.py`: `BlockStore._announced_rejections`
+ `_announce_rejection()`; both REJECTED prints (bad-alignment and
cross-timeframe-duplicate) now announce once per process (in-memory, a
restart re-announces once by design). Verified: 30 sync cycles -> 1 line
(was 30), Block still 55 zones, fresh instance re-announces once.
Not committed yet.
Side facts: the V5S tv_scraper zone file was ~3h stale but 8 V5S
processes are running (its normal set of 8), so the scraper is very
likely up and just idle while the market's closed (not individually
verified). No V6S process left running afterward; V5S untouched.

## TM-STR design discussion IN PROGRESS (2026-09-20) -- nothing built yet

User asked "can we make TM-STR" (earlier said "dont suggest TM now, RM first"
-- that hold is over). Explained V5S's TM-STR from its own main.py
header (trend-following: M5 parent bias, M3 entry triggers, one trade per
M5 event, SL = parent line +/- buffer, post-entry breakeven/partials
(70% @+10, 15% @+15)/M3-far-line trailing, opposite -> square off +
reopen, ICT Guard). Flagged honestly that V5S's design sits on exactly the
modules just deleted (structure.py, st_bridge.py, bridge_bar_flip.py --
restorable from git, last present in e0a6cb1). User chose: "what are the
logics we following in TM-STR?? can we discuss and rebuild based on
that??" -- i.e. walk the V5S logic, discuss each part, rebuild from that,
NOT a blind port and NOT a blank-page redesign. ONE decision at a time.
**Decided so far**:
1. BIAS (M5 parent) = ATR dual-trail + CISD on M5, most recent event
   wins (matches what the user defined earlier: ATR flip up = strong,
   down = weak, trap keeps the previous direction, bullish/bearish CISD
   sets direction). Supertrend held back for later.
**Still to discuss, in this order**: (2) M3 entry timing (V5S fresh-flip
+ catch-up 2pt/40% triggers vs CISD-based); (3) one-trade-per-M5-event
eligibility; (4) initial SL (V5S: parent line +/- buffer; RM now uses
lines->swing); (5) post-entry mgmt (breakeven/partials/trailing -- same
open question as RM's, still bridge-based `m3_far_line`); (6) lifecycle
on opposite signal; (7) ICT Guard (off for RM). **Open technical
question to raise before building**: V5S deliberately moved M3/M5 flip
detection OFF copy_rates recompute and ONTO the live bridge because the
recompute diverged from the live chart (ATR-300 / line2 drift). RM's HTF
states use native recompute and the user accepted a similar one-bar drift
for Supertrend -- but for TM's M5/M3 flip EVENT TIMING (which drives
"fresh" triggers) that drift matters more; needs an explicit decision.
**Known V5S bugs to design around**: (a) initial SL must be FROZEN at the
deciding bar, not a live-drifting read; (b) the "fresh" trigger needs a
bar-close gate -- V5S fired a real trade 47 min after its own flip.

**TM-STR timeframes CORRECTED (2026-09-20)** -- supersedes decision #1's
"M5 parent". User: "as of now m15 is primary structure and bias, m5 is
execution, m3 is also execution - lets add later". So: BIAS = M15
(ATR dual-trail + CISD on M15, most recent event wins -- exactly the "M15
Primary Structure" rules the user defined earlier; Supertrend later);
EXECUTION = M5 (the role M3 had in V5S); M3 = a SECOND execution
timeframe to add LATER -- build execution with the timeframe as a
parameter/list ((5,) now, (5, 3) later) so that's config, not a rewrite.
Everything V5S called "M5 parent / M3 execution" shifts up one level to
"M15 parent / M5 execution". The M3-entry-timing question was asked and
the user picked "Something different" and answered with THIS timeframe
correction instead of describing trigger rules -- the actual M5 entry
EVENT/trigger is therefore still UNDECIDED (re-asked with corrected
timeframes).

**TM-STR SPEC GIVEN BY USER (2026-09-20), verbatim intent** (after two
"Something different" answers to my option questions, user finally wrote
the rules themselves):
- BIAS: "m15 atr dual flip, strong/weak; m15 cisd, bullish/bearish;
  recency wins" (M15 = primary structure + bias).
- EXECUTION: "purely based on m15": "buy on bullish m5 cisd - if m15
  favours; sell on bearish m5 cisd - if m15 favours".
- SL: "atr far line, or supertrend line if available in m5, or check for
  same in m15 if available".
- ELIGIBILITY: "one trade per m5 flip or cisd change".
**My readings (NOT yet confirmed)**: "available" = line on the CORRECT
side of entry (below for BUY, above for SELL) like RM; M5 CISD entry uses
the fresh (momentary) CISD contract like RM.
**Open ambiguities, asked ONE at a time**: (1) is an M5 ATR FLIP also an
entry trigger, or only M5 CISD -- "one trade per m5 flip or cisd change"
hints the flip is an event too; (2) if both an ATR far line and a
Supertrend line are usable on M5, which wins; (3) what if neither M5 nor
M15 has a usable line (RM falls back to CISD swing); (4) still undecided:
opposite-bias lifecycle (V5S squared off + reopened), post-entry
breakeven/partials/trailing, ICT Guard, and native-vs-bridge for M5/M15
flip timing.

**TM-STR decisions CONFIRMED so far (2026-09-20)** -- answers to my
one-at-a-time questions on the spec above:
- ENTRY TRIGGER = ONLY a fresh M5 CISD. An M5 ATR flip is NOT an entry
  trigger. Each new M5 CISD is its own event => one trade per M5 CISD
  (a fresh CISD in the same direction as the last one may trade again).
  M15 must favour (bullish CISD needs M15 bullish; bearish needs M15
  bearish) -- M15 bias = ATR dual flip + CISD on M15, recency wins.
- SL: candidates = M5's ATR lines + M5's Supertrend line that are on the
  CORRECT side of entry (below for BUY, above for SELL); if several are
  usable take the FARTHEST from entry (widest SL -- user's explicit
  choice). If M5 has none usable, do the same on M15. If neither has
  one, fall back to the triggering CISD's swing low/high +/- buffer (like
  RM); if no active swing either, SKIP the trade. Buffer applied on top.
  (My interpretation: "atr far line" = treat both ATR lines as
  candidates and take the farthest usable; equals the far line in normal
  states.) This is the SAME line->line->swing machinery as RM's
  `sl_basis.py`, but with M5 -> M15 as the timeframes instead of M5 -> M3
  and NO "must be tighter" filter (that filter is RM-ICT-only).
- Timeframes: bias M15, execution M5; M3 execution added LATER (build the
  execution timeframe as a parameter).
**Still to decide**: opposite-signal / M15-bias-flip lifecycle; post-entry
breakeven/partials/trailing; ICT Guard; native-vs-bridge for flip timing;
also whether "one trade per event" persists across restarts (V5S did).

- LIFECYCLE (confirmed 2026-09-20): when the M15 bias flips AGAINST an
  open TM-STR trade, CLOSE IT IMMEDIATELY (chosen over V5S's "only close
  when an opposite entry fires" and over "leave it to SL/trailing"). A
  new trade in the new direction only comes from the next matching M5
  CISD while M15 favours it. My reading: "flips against" = M15's winning
  direction (ATR-dual/CISD recency) changes; a change of winning SOURCE
  with the SAME direction does not close anything. Closes the whole
  remaining position incl. after partials. NOT yet confirmed with user:
  the exact treatment of that reading.

- POST-ENTRY MGMT (confirmed 2026-09-20): V5S's scheme with TRAILING ON
  M5: breakeven at +10pts (or after the first partial), close 70% at +10
  and a further 15% at +15, the last 15% trails M5's far ATR line minus
  buffer; SL only tightens; a manual SL edit pauses trailing; computed
  natively from MT5 data (no chart). Reuses `sl_manager.py`/
  `trade_manager.py` unchanged. NOTE this is a DIFFERENT trailing source
  than RM's current `m3_far_line` (live-bridge, M3) -- TM-STR needs its own
  native "M5 far line" source; RM's open post-entry question is separate.
- ICT Guard for TM-STR: NOT asked. Default plan = OFF like RM (user's
  "no guard as of now" was scoped to RM); state this assumption in the
  final recap so they can redirect.

- M15 ATR-DUAL SOURCE (confirmed 2026-09-20): user chose the LIVE MQL5
  BRIDGE (like V5S), NOT native recompute -- matches their chart exactly;
  needs the ATR dual indicator attached+running on the M15 chart AND the
  persisted flip tracker (`bridge_bar_flip.py`, deleted in bafd0e7, to be
  RESTORED from git e0a6cb1). Consequence to handle: the tracker only
  builds history once running -- a COLD tracker bootstraps "confirmed
  since NOW", which would give the ATR side a fake brand-new event time
  and wrongly beat an older-but-real CISD in recency arbitration (this
  exact artifact bit me on 2026-09-18 diagnosing structure). Plan: seed
  V6S's tracker state file ONCE from V5S's real
  `v5s_*_bridge_bar_flip_state.json` M15 entry (a one-off warm-start
  copy, not shared live state) -- tell the user.

## TM-STR BUILT (2026-09-20) -- NOT committed yet, NOT run live

Built from the rule-by-rule discussion above (NOT a port of V5S main.py).
New files: `trend_config.py` (per-symbol `TMSymbolConfig`, magic
**26091803**, bias M15, exec (5,), trailing M5, env prefix
`V6S_TM_{SYMBOL}_`), `trend_bias.py` (`compute_bias`: M15 ATR-dual via
the live bridge tracker vs standing M15 CISD, most recent wins, tie->ATR,
returns None if ATR has nothing -- no guess), `trend_entry.py`
(`find_signal` + `TrendEligibilityStore`: fresh M5 CISD matching M15 bias,
one trade per M5 CISD event persisted so restarts can't re-fire, SL via
`sl_basis` searching M5 then M15 then CISD swing, no line+no swing = no
trade AND event not consumed), `trend_main.py` (`python -m
v6_sentinel.trend_main`: per-symbol runtime; run_once = (1) bias flipped
against open trade -> close immediately, (2) fresh-M5-CISD entry, (3)
manage: V5S sl_manager/trade_manager unchanged, trailing on NATIVE M5 far
ATR line via `_trailing_far_line`). RESTORED `bridge_bar_flip.py` from
e0a6cb1. `sl_basis.initial_sl_basis()` gained a `timeframes` param
(default (5,3) so RM is unchanged; TM passes (5,15)). Comments
`V6S-TM-STR-{trigger}[-P1|-P2|-BF]`.
**Verified**: 18 synthetic bias/entry/eligibility/SL checks + 12
fake-broker lifecycle checks (entry, no-repeat, restart persistence,
decision-only makes ZERO broker calls, flip-close, flip+reverse in one
cycle, redundant signal, rejected order retried, no-bias leaves trade
alone, partial 70% @+10 then breakeven SL) -- all pass; the lifecycle
test hard-guards against real orders (all order fns faked, `mt5.order_send`
booby-trapped). Live real-data wiring check (scratch state, trading off):
bias resolved BULL/ATR from a tracker seeded from V5S's live reversal
tracker (event Fri 09-18 21:45 IST), native M5/M15 lines computed,
`run_once` completed clean. **Tests are throwaway scripts in the session
scratchpad -- NOT saved in the repo.**
**Untested**: real signal path in live market hours (weekend: CISD
bridge stale, so no CISD -> bias came from ATR alone); real order
placement; M15 bias with a real fresh CISD.
**ASSUMPTIONS the user has NOT confirmed** (flag them): ICT Guard OFF for
TM; magic 26091803; "flips against" = M15 winning DIRECTION changes;
"atr far line" = both ATR lines are candidates, farthest usable wins;
trailing + SL lines are NATIVE (user only chose bridge for the M15 ATR
flip); SL-line timeframes fixed (5,15) with a per-exec-tf dict in
trend_entry.
**KNOWN GAPS**: (1) NO staleness alert for the M15 ATR bridge -- if that
chart/indicator stops, the tracker can't advance, the bias silently
FREEZES and TM keeps trading on it (V5S wired StaleAlertTracker + Telegram
for exactly this); (2) tracker COLD START: must seed
`v6s_trend_manager_bridge_bar_flip_state_XAUUSD.json` from the LIVE V5S
REVERSAL tracker's "15" entry (the V5S TREND tracker's M15 entry is STALE,
~Sep 11) AT LAUNCH TIME, because the tracker only evaluates the latest
closed bar so a long downtime can skip flips; (3) the "skipped, no SL"
case prints nothing (event just isn't consumed); (4) M3 execution not
added (by design).

## TM-STR: stale-feed watch + saved test suite (2026-09-20, same day)

User asked to do gap #1 (stale bias feed) and #3 (save tests), then
commit/push/sync.
**#1 DONE**: `BiasFeedWatch` in `trend_main.py` (pure logic, injectable time
+ tick_time) watches `bridge.read_lines(symbol, bias_timeframe)` (the M15
ATR bridge). Fault = feed stale for >= 60s (`STALE_FEED_SECONDS`) WHILE
prices are ticking; then ONE alert (print + decision log + Telegram) and NEW
ENTRIES PAUSED; open trades still managed AND a bias-flip close still works
while paused. Sends one "resumed" alert on recovery; re-arms for a second
outage. "Ticks flowing" = an OBSERVED CHANGE of `symbol_info_tick().time`
within 120s (first tick is only a baseline) -- deliberately based on the
tick time CHANGING, not its age, so it doesn't depend on the broker server
clock; a closed market/weekend never alarms or blocks. NOTE the "pause new
entries" part was my reading of "alert (and possibly a pause)" -- user said
"do 1" without choosing; flagged in the reply, easy to flip.
**Not covered (told the user)**: the M15 CISD feed itself is NOT watched --
if that indicator stops, `read_cisd` returns None and the bias silently
ignores CISD flips (ATR alone decides). Watching it needs a freshness check
distinct from "last_cisd == none". M5 CISD staleness is harmless (no fresh
CISD = no entry).
Shared helper: new `alerts.py` (`send_alert`, Telegram best-effort, never
raises); `reversal_main` now imports it instead of its own copy.
**#3 DONE**: first real test suite in the repo: `tests/` (stdlib `unittest`,
NO pytest in this env; run `python -m unittest discover -s tests -t . -v`
from repo root). 53 tests: `test_v6s_tm_logic.py` (bias arbitration, entry
gating, eligibility incl. restart persistence, SL selection, feed-watch
state machine incl. closed-market/weekend), `test_v6s_tm_lifecycle.py`
(fake-broker run_once: entry/no-repeat/decision-only/reject-retry/redundant/
flip-close/reverse-in-one-cycle/partial+breakeven/stale-pause), `test_v6s_rm.py`
(RM-ICT SL override incl. the exact 22pt case, shared line logic unchanged for
RM-STR, Block rejection announced once). The lifecycle tests run with
enable_trading=True so setUp replaces every order path with a mock,
booby-traps `mt5.order_send`, and asserts it before any test body.
**Mutation-checked**: broke the code 5 ways (drop the tighter-SL check, drop
the stale pause, flip the bias tie-break, restore the spam print, allow
same-CISD refire) -- the suite caught every one. LESSON: a mutation harness
that restores a same-size file within the same second can leave Python's
stale bytecode of the BROKEN version in place (mtime is whole seconds +
size) -- it produced a false "1 failure after restore"; clearing
`__pycache__` / `python -B` showed the source was fine.
Not covered by tests: `reversal_entry` (RM-STR), `trend_bias` against a real
tracker, real order placement, live-market signals.

## TM-STR: "MORE M5 EXECUTION RULES" (2026-09-20) -- RESOLVED: original logic kept
The user's extra rules ("if price is already strong and a bullish cisd occurs
buy", "flip + cisd same candle -> fire", "strong but opposite cisd: no sell,
NOT closing buy, wait for structure to shift weak to close") are all just the
ORIGINAL logic restated: entry = fresh M5 CISD + M15 bias favours; exit = M15
bias flip ("structure" = M15's ATR-dual/CISD structure). The user then
confirmed explicitly: "the m5 bullish cisd can fire a buy trade even if price
is under dual atr lines, if m15 favours" (mirror for sells) -- the M5 ATR
strong/weak state is NOT an entry gate and there is NO M5 structure-shift exit.

**Misreading to not repeat:** I first built an M5-state entry gate + an M5
structure-shift close (+ per-feed M5 watch, exit_structure_timeframe config).
It contradicted the user (it would block a BUY under both lines, and close it
the next cycle even if it fired). Discarded before commit; the attempt is kept
only as a patch in the session scratchpad (m5_state_gate_attempt.patch). Lesson:
when a rule list restates a design already agreed, check it against the agreed
logic (and ask which "structure"/"price is strong" means) BEFORE building a new
mechanism from it.

**Tests added (59 total pass):** BUY fires with price under both M5 ATR lines
(SL falls to M15 line / CISD swing), SELL mirror; an opposite M5 CISD against
the M15 bias neither opens a trade nor closes the open one.

## TM-STR: SQUARE-OFF rule (2026-09-20, after the "resolved" note above) -- BUILT, UNCOMMITTED
User: "what can square off the trade is: if m15 bearish/weak and m5 is strong
structure, but a bearish cisd happens on m5, price still above both atr lines,
so we take the sell (primary structure + m5 cisd). After the sell, price
immediately creates a bullish cisd -> this squares off the trade, as the m5
bullish cisd qualifies the buy trade as price is already above atr lines.
Similarly for sell side."
**Rule as built:** the ONLY close besides the M15 bias flip = a fresh M5 CISD
opposite to the open trade AND the M5 ATR-dual confirmed state agrees with that
CISD (strong+bullish closes a SELL, weak+bearish closes a BUY). M15 is NOT
consulted for it, and no reverse trade follows unless the normal entry rule
(M15 favours) allows one. Opposite CISD with the state disagreeing closes
nothing (wait for the structure to shift). Entry still has NO M5-state gate.
Code: `trend_entry.find_squareoff`, `trend_main` step 1b, config
`squareoff_timeframe=5`, comment suffix "-SQ", decision-log event `squareoff`.
M5 state = confirmed state of the persisted bridge tracker (trap keeps the
previous state) -- NOT a literal live price-vs-lines test (assumption, flagged).
M5 ATR feed has its own BiasFeedWatch (`rt.state_feed_watch`): stale ->
square-off paused + one alert; entries unaffected. 75 tests pass; 6/7 mutations
caught (the 7th is equivalent). Launch: seed BOTH "15" and "5" of TM's tracker
state file from V5S's live reversal tracker.

### Square-off assumptions RESOLVED by the user (2026-09-20)
- "M5 state" = the CONFIRMED state as of the last closed candle, ALWAYS --
  "candle close always, nothing to do with live price on or above the levels".
  (Between the two lines the confirmed state is unchanged; that still counts.)
- An M5 FLIP itself closes the trade straight away ("the M5 flip itself closes
  the SELL"): `trend_entry.find_flip_exit`, trend_main step 1b, comment "-MF",
  decision-log `m5_flip_exit`. Built as an EVENT (a FLIP -- not TRAP_RESOLVED --
  against the trade, on a bar that CLOSED after the position opened:
  bar_time + tf*60 > position.time), NOT a state comparison -- a state check
  would close a BUY opened under both lines (weak state) the very next cycle,
  which contradicts the entry rule. MT5 position.time and bar times share the
  server-time base (verified 15/15 real deals against M1 bars).
- So a trade now closes three ways: M15 bias flip (-BF), M5 flip (-MF),
  M5 square-off CISD (-SQ). Both M5 exits pause together when the M5 feed is
  stale (alert once). 95 tests pass; 12/12 mutations caught.
- STILL OPEN (asked, unanswered): does a square-off also OPEN the opposite
  trade? Built as NO (no reverse unless M15 favours the new direction).
- Not decided, built as "no": TRAP_RESOLVED against a trade does not close it.

### tests/ folder deleted (2026-09-20)
The user deleted the whole `tests/` suite (75->95 tests at the end, incl. RM tests) -- it still exists in git history at b809556 (53 tests) if ever wanted back. No test files remain in the working tree; the notes above that cite test counts / mutation checks describe verification that was run and then removed.

### Decisions 2026-09-21
- SL manager: NOT something to port -- V6S sl_manager.py/trade_manager.py were already verbatim ports of V5S (code-identical apart from the [V6S-SL] log prefix). Only V5S ict_sl_manager.py (3-stage, TM-ICT) is missing, and it belongs with TM-ICT.
- TM-ICT is DEFERRED by the user ("will plan later, not now"). Do not propose or start it. When it comes back, first decide whether it reuses V6S RM-ICT zone infra (nlb_nsb_block) or V5S ict_ob_block + ob_bridge_lite.
- Remaining V5S pieces not in V6S: Exit Manager (cross-component opposite-entry square-off), Watchdog (stale heartbeat alerts), critical-alerts watcher/listener/subscribers.

### Trade journal (2026-09-21) -- BUILT, UNCOMMITTED
User: "every trade will have the logic when entered and exited, to review later". Built v6_sentinel/trade_journal.py: one JSONL per component per symbol (v6s_trend_manager_trade_journal_<SYM>.jsonl, v6s_reversal_manager_{str,ict}_trade_journal_<SYM>.jsonl; gitignored). Events: entry (full "logic" incl. bias/CISD/zone/level/SL source), partial, sl_move, exit_requested (bot own close reason), exit (written by reconcile() from broker deal history: bot reason or SL_HIT/TP_HIT/STOP_OUT/MANUAL, entry/exit price, net profit, duration, and a copy of the entry logic). Only real fills journaled (decision-only has no ticket). Reconcile finalizes only when deal history proves full close, throttled 30s/ticket. Wired into trend_main + reversal_main (both components). Verified with a fake broker + read-only against real MT5 deal history, then the check scripts were deleted (no tests kept, per [[feedback-test-cleanup]]). Raw times inside logic are broker-server epochs; time_ist per line is wall-clock IST.

### V6S Scraper (2026-09-21) -- ported, UNCOMMITTED, NOT YET CUT OVER
User: "change the name to V6S Scraper". Copied v5_sentinel/tv_scraper -> v6_sentinel/tv_scraper with V6S naming: run `python -m v6_sentinel.tv_scraper.scraper`, env prefix V6S_TV_SCRAPER_* (10 mirror lines appended to .env, same values: real Brave profile, CDP port 9223, window 822,-1440 3440x1440, grid 2x4), state files v6s_tv_scraper_{zones,first_seen,retest,live,mitigation_track}.json + zone_history.jsonl, log tag [V6S-TVZ]; .gitignore gained v6s_tv_scraper_profile/. V6S nlb_nsb_watcher now reads v6s_tv_scraper_zones.json (env V6S_TV_SCRAPER_ZONE_STATE_FILE) instead of V5S file. V5S scraper still RUNNING and untouched. ONLY ONE scraper can run at a time (same Brave profile + CDP port). CUTOVER (needs user go-ahead): stop v5_sentinel.tv_scraper.scraper -> copy v5s_tv_scraper_*.json(l) to v6s_tv_scraper_* (keeps retest/first-seen/mitigation state) -> start v6_sentinel.tv_scraper.scraper (window must stay maximized). Until cutover the V6S watcher has no zone feed. After cutover the v5_sentinel folder is no longer needed by V6S.

### V6S went LIVE on demo (2026-09-21 ~02:57 IST, user: "commit push and sync local, enable trading now")
- Committed/pushed 3ac87bc (V6S Scraper). Terminal account 83124895 Exness-MT5Trial12 (demo, $2000). V5S bots STOPPED (watchdog, critical_alerts_watcher, exit_manager, trend_manager_ict, reversal_main, main, nlb_nsb_watcher) -- V5S tv_scraper (v5_sentinel.tv_scraper.scraper) and v3 processes deliberately left running. V5S_*_ENABLE_TRADING flags are still true in .env (just not running).
- Started (background, logs v6s_*_run.log in repo root): v6_sentinel.nlb_nsb_watcher (env V6S_TV_SCRAPER_ZONE_STATE_FILE=v5s_tv_scraper_zones.json -- reads the still-running V5S scraper until the V6S-scraper cutover), v6_sentinel.reversal_main (V6S_RM_XAUUSD_ENABLE_TRADING=true), v6_sentinel.trend_main (V6S_TM_XAUUSD_ENABLE_TRADING=true). Enable flags passed via LAUNCH ENV ONLY, NOT written to .env, so a restart is decision-only unless re-enabled. TM tracker was seeded from V5S live reversal tracker (M15 strong, M5 weak). Each process appears twice in the process list = python launcher + child, not duplicates.
- Lots left at the coded default 0.01 (user never answered the 0.06 question): partial1 (70%) rounds to the whole 0.01 position, partial2 to 0. To change: V6S_TM_XAUUSD_LOTS / V6S_RM_XAUUSD_LOTS at launch.
- Still to do: check feeds fresh after market open 03:30 IST; V6S-scraper cutover; first live signals unverified.

### Update 2026-09-21 ~03:10 IST: V5S fully retired, V6S Scraper live, lots 0.06
- ALL V5S processes stopped (incl. v5_sentinel.tv_scraper.scraper). V6S Scraper (python -m v6_sentinel.tv_scraper.scraper, log v6s_tv_scraper_run.log) now runs, attached over CDP 9223 to the SAME existing Brave/TradingView tab (browser persists across scraper restarts); state carried over v5s_tv_scraper_* -> v6s_tv_scraper_*. V6S watcher restarted WITHOUT the env override (reads v6s_tv_scraper_zones.json). v3.tradingview_bot.main + v3.tv_bridge.receiver (unrelated TradingView-webhook logger, no trading) left running.
- Lots changed to 0.06 in trend_config.py and reversal_config.py defaults (user: "change lot size to 0.06") -- UNCOMMITTED at time of writing. TM+RM restarted with V6S_*_ENABLE_TRADING=true via launch env (not .env). Processes running now: v6_sentinel.tv_scraper.scraper, nlb_nsb_watcher, reversal_main, trend_main (each appears twice = launcher+child). V6S still has no watchdog / Telegram critical-alerts process.

### FIRST LIVE TRADES + RM-ICT STALE-ZONE BUG (2026-09-21 03:35 IST, market open)
- RM-STR BUY (M30 ATR line 4374.094 + M5CD) and RM-ICT BUY (H4 bull OB [4223.5-4249.5] + M5CD) both filled 03:35 @4381.45 lots 0.06, SAME SL 4378.532 (M3/ATR1 line: for ICT the zone-edge SL was >15pts so the line-first override replaced it). Both stopped out after 61s, -17.95 each. TM-STR made no trade.
- ROOT CAUSE of the ICT trade: reversal_ict treats zone.retested as the touch and relies on "only a CISD firing on a later cycle can be caught". But ALL 21 retested zones in v6s_nlb_nsb_block_XAUUSD.json have retested_source="seed" (touch 49h..3748h old, copied from the scraper), none "live". So any long-ago-touched zone + the first fresh M5 CISD after start = a trade, and one CISD marked ~10 stale bull zones as traded. 7 stale BEAR retested zones (H4 [4377-4403], H2, M3 ...) were still armed to fire SELL. Same stale-retest family as the V3 incident (fix there: absolute recency check on retested_at).
- RM STOPPED (no trading) right after; TM-STR + watcher + scraper still running. Fix plan: a touch only counts if seen LIVE by the watcher (not seed) AND within a max age N min; N needs the USER (V3 used 30 min). Ask before restarting RM live. Also note RM-STR/ICT line-first SL can be very tight (2.9pts here).

### RM-ICT stale-touch FIX implemented (2026-09-21, user confirmed 30 min) -- UNCOMMITTED, RM still STOPPED
reversal_ict._touch_is_current(): a zone is a candidate only if retested_source=="live" AND retested_at within cfg.ict_touch_max_age_minutes (30.0, env V6S_RM_XAUUSD_ICT_TOUCH_MAX_AGE_MINUTES) of now (both wall-clock). Seed retests never count. Verified: real block -> 0 signals; synthetic live-5min fires, live-45min/seed/untouched do not (check script deleted). Wired via reversal_config + reversal_main. NOT yet done: restart RM live (waiting for user go-ahead), commit/push. OPEN HEADS-UP for the user: RM-STR touch arming (htf_levels.LevelEligibilityStore.mark_touched) has NO time bound -- a level stays armed until its value changes (ATR trail lines can stay constant for hours), same stale-touch family; ask before changing.

### BRIDGE-ONLY for M1/M3/M5/M15 (2026-09-21, user: "atr data, supertrend data to be strictly taken from mt5 bridge, no internal computation") -- BUILT, UNCOMMITTED
- bridge.py: BRIDGE_ONLY_TIMEFRAMES=(1,3,5,15); new read_atr_dual() (line values + per-line trend/event_time + close/bar_time) and read_supertrend() (SUPERTREND_<sym>_<tf>.json) with the same freshness gate as read_lines; stale/missing = contributes nothing, NO native fallback.
- Call sites switched: sl_basis.line_values (SL lines for TM M5/M15, RM M5/M3), trend_main._trailing_far_line (TM trailing M5, via bridge_flip.far_near), htf_levels for RM-STR M15+M5 (_compute_htf_state_from_bridge: values/roles from bridge, roles vs bridge last-closed close, ATR flip state/event time from a persisted BridgeBarFlipTracker -- RM got its own tracker file v6s_reversal_manager_bridge_bar_flip_state_XAUUSD.json seeded from TM tracker keys 15+5). M30/H1/H2/H4/D1/M10 remain NATIVE (user only named 1/3/5/15). Verified with the native functions booby-trapped; check script deleted.
- TM restarted with new code (trading env on). RM still STOPPED. STILL OPEN: (1) the closed-candle usable-line rule for SL (proposed, user has not confirmed; bridge lines carry a per-line trend flag that could drive it), (2) RM restart decision, (3) commit/push.

### SL usable-line rule fixed to CLOSED candles (2026-09-21) -- BUILT, UNCOMMITTED
User challenged how a M3 line was seen as support ("never came down as support on any candle close ... candle pulled in and closed under the atr"). Findings: the old sl_basis rule compared ONLY the live entry price (ask 4381.426, a spike) to the line; M3 ATR1 4380.532 was trend -1 (resistance) on every closed candle (last closed M3 close 4374.442). The MQL5 bridge publishes the LAST CLOSED bar (closed_idx = rates_total-2) so it never shows the forming-bar flip the chart shows; that intrabar "line comes down" is chart-only. FIX: sl_basis._pick now also requires the line to be on the correct side of the last CLOSED candle close of that timeframe (bridge `close`; below it for a BUY, above for a SELL), else the timeframe is skipped -> falls to next TF then CISD swing. Replayed the incident: BUY now -> SWING; genuine support/resistance lines still used. Shared code: applies to TM-STR, RM-STR, RM-ICT. TM restarted with it; RM still STOPPED; nothing committed yet.

### Confirmed 2026-09-21: RM touch detection is purely LIVE (user: "nope rm is purely based on live touch")
RM-STR level touches (live bid/ask reaching the line) and RM-ICT zone touches (live tick entering the zone) stay live-tick by design -- do NOT convert them to candle-close. Candle-close-only applies to: TM-STR (bias, entry CISD, exits), the SL line "usable" test (closed-candle side), and CISD confirmations. Also kept: the closed-candle SL-usable-line rule. Open, unanswered: optional guard that the CISD swing SL must be on the correct side of entry.

### 2026-09-21 ~04:20 IST: committed c38ee94 (ICT 30-min live-touch, closed-candle SL lines, bridge-only M1/M3/M5/M15) and RM restarted live
- RM restart surfaced a side effect of the bridge switch: levels-state key "5:ATR" had event_time 1789756800 (old native) vs the bridge tracker 1789756500 -> the eligibility store logged a "regressed ... transient glitch" warning every cycle. Fixed by stopping RM and setting that ONE stored value to 1789756500 (traded_directions/touched_lines untouched). A M15/SUPERTREND reset also happened once at first start (harmless, no traded directions). If bridge-vs-native event times ever mismatch again after a source change, align the stored event_time (RM stopped) instead of resetting traded state.
- Running: tv_scraper, nlb_nsb_watcher, reversal_main, trend_main (all trading env on, lots 0.06); v3 tradingview_bot + tv_bridge receiver untouched. No V6S watchdog yet.

### EXIT MANAGER built (2026-09-21 ~08:20 IST, user: "yes build exit manager") -- UNCOMMITTED, running DECISION-ONLY
v6_sentinel/exit_manager.py + exit_manager_config.py. Run: python -m v6_sentinel.exit_manager (env V6S_EM_XAUUSD_ENABLE_TRADING=true for real closes; default off). Rule (V5S-style, symmetric): a REAL fill journaled by TM-STR / RM-STR / RM-ICT (trade_journal "entry" events, tailed with a persisted per-source watermark + processed-ticket set; bootstrap = now, so no history replay) closes every OTHER component's open opposite-direction position, full volume, comment V6S-XM-SQ-<source>. Improvements over V5S: only positions opened BEFORE the triggering entry (same-poll opposite entries cannot annihilate each other; later fill wins) and the reason is written into the closed trade's own journal as exit_requested EXITMANAGER (trade_journal.reconcile now also reads exit_requested written by another process via _pending_from_file). TM/RM restarted to load that. Verified with a fake-broker check (deleted). Started decision-only because closing real trades needs the user's explicit go-ahead. Existing opposite pair at build time (RM-STR SELL 0.01 + RM-ICT BUY 0.06) is NOT acted on -- only future entries trigger it.

### Exit Manager: TIMEFRAME HIERARCHY rule (2026-09-21, user: "smaller time reversal trade cannot close the htf trade, but a htf trade can close a smaller time frame trade")
exit_manager.py now closes an opposite, older position only if the NEW trade timeframe >= that position timeframe. Trade timeframe: RM-STR = level timeframe_minutes (journal logic), RM-ICT = zone timeframe parsed from zone_id "XAUUSD|<tf>|...", TM-STR = FIXED M15 (trend_config.bias_timeframe) via WatchedSource.fixed_timeframe_minutes -- ASSUMPTION (TM records only its M5 execution tf). Equal timeframes CAN close each other -- ASSUMPTION. Unknown timeframe = never closed. Blocked closes are logged as close_blocked_by_timeframe. 10-case fake-broker check passed and was deleted. Exit Manager still runs DECISION-ONLY (needs V6S_EM_XAUUSD_ENABLE_TRADING=true + explicit user go-ahead); uncommitted.

### Timeframe hierarchy applied INSIDE RM too (2026-09-21; user: "m3/m1 qualifies a Buy trade i meant" -- HTF sell open, LTF buy qualifies: both open, LTF cannot close HTF) -- UNCOMMITTED
Found: RM own _process_signal squared off its own opposite position regardless of timeframe (07:57 RM-ICT M3-zone BUY closed the RM-ICT M5-zone SELL runner). Fixed in reversal_main._process_signal: opposite positions of the SAME component are closed only if signal_tf >= position_tf (equal allowed); a smaller-tf signal leaves the HTF trade and still OPENS its own -> a component may now hold one position per direction; new logic order = close eligible opposites, then if same-direction open -> redundant, else open. run_once now manages EVERY open position (loops, not [0]). Shared helper trade_journal.timeframe_of_logic + TradeJournal.timeframe_minutes(ticket) (used by RM and exit_manager). Unknown-timeframe position = legacy (closed). Failed close -> no open, setup not consumed. 9-case fake-broker check passed (deleted). RM + Exit Manager restarted (EM still decision-only). TM untouched (its own single-slot bias logic).

### Exit Manager LIVE (2026-09-21, user: "yes run it live") -- committed af2e0a0
Started with V6S_EM_XAUUSD_ENABLE_TRADING=true via launch env only (not .env; a restart is decision-only unless re-enabled). Pre-checked: demo account 83124895, no unprocessed journal entries, so no surprise close at start. Live processes now: tv_scraper, nlb_nsb_watcher, trend_main, reversal_main, exit_manager (all with trading env on; lots 0.06). Still no V6S watchdog / critical-alerts.

### CONFIRMED by the user 2026-09-21 09:10 IST: TM-STR square-off rule is "the perfect rule" -- do not change it
A live case (SELL open, bullish M5 CISD confirmed, but M5 state still WEAK with the last close under both M5 ATR lines, M15 bias SELL) correctly did NOT close the SELL and did NOT open a BUY. The trade stays until an M5 ATR flip to strong after entry, an M15 bias flip, an Exit Manager close, or the SL. An opposite M5 CISD closes only when the confirmed M5 state agrees with it.

### Touch detection now WICK-AWARE (2026-09-21 ~10:00 IST; user: "Yes fix") -- UNCOMMITTED
Bug found: RM-STR H1 BUY missed. Price wicked to 4355.967 (0.10 through the H1 ATR1 support 4356.071) at 09:37:55.658-.772 IST -- 7 ticks in 0.11s -- but RM/watcher sample price ONCE per second, so no touch was armed; the M3 bullish CISD at 09:45 then had nothing to confirm. Fix: broker.price_extremes_since(symbol, since_msc) = (lowest bid, highest ask, newest time_msc) over every tick AFTER the previous cycle (copy_ticks_range); RM run_once keeps rt.last_tick_msc (first cycle = start from now, never history) and passes bid_low/ask_high to reversal_entry.scan_touches (support uses bid_low, resistance ask_high); the watcher keeps _last_tick_msc per symbol and passes them to BlockStore.update_live (zone touch AND invalidation use the extremes, so a wick through a zone deletes it). Still a LIVE touch (user: RM is purely live-touch) -- just no longer blind to sub-poll wicks; nothing older than one cycle can arm retroactively. Verified against the real 09:37 ticks + fake states (script deleted). RM + watcher restarted.

### BUG: false TM BIASFLIP closes at every M15 bar boundary -- FIXED (2026-09-21 ~14:55 IST) -- UNCOMMITTED
User asked the reason for a BUY close: TM-STR BUY (14:40-14:45, +3.34) closed by BIASFLIP "SELL (ATR event 08:45)" although the standing M15 bullish CISD (13:45) was unchanged. Same at 07:15:00 (BUY +6.86). ROOT CAUSE (measured with a 10 ms probe at the 14:50 M5 boundary): bridge files carry bar_time = OPEN time of the last CLOSED bar, so now-bar_time is 1..2 bar lengths and hits exactly 2 the instant a bar closes; bridge._bar_time_stale (limit tf*60*2.0) therefore returned STALE for ~0.45 s (+0.02s..+0.47s) at EVERY bar boundary for all readers (ATR lines, Supertrend, CISD). TM polled inside that window, read_cisd -> None, compute_bias fell to ATR alone (older SELL event) -> instant BIASFLIP close. FIX: bridge.BAR_STALENESS_GRACE_SECONDS = 30 added to the limit (comment in bridge.py corrected). Effects elsewhere were harmless (entries need fresh CISD, RM just skips a cycle). TM + RM restarted. Other BUY closes that day: RM-STR H1 BUY 10:55 SL -57; RM-ICT BUY 13:04 SL -51; RM-STR H2 BUY 12:35 still open. Possible extra hardening (not done): hold the last standing CISD for a few seconds if a read fails, and/or require the bias flip to persist 2 cycles.

### DECISION 2026-09-21: skip the optional "hold last known CISD / require bias flip to persist" hardening
User: "skip it, if necessary will add later". The bridge staleness grace (df8bec0) is the fix; probe at 14:55 showed no dropout. Only revisit if a false TM BIASFLIP appears again (the trade journal exit_requested BIASFLIP lines, bias_source ATR with an old event_time, are the tell). Also committed 6a0759e (wick-aware touches). Not built: V6S watchdog / critical-alerts.
