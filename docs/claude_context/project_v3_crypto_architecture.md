---
name: project-v3-crypto-architecture
description: Agreed layered architecture for the new TradingView-sourced multi-symbol trading system, named "V3 Sentinel" (Data Bridge -> Signal Engine -> Execution Bridge + Alert Manager)
metadata: 
  node_type: memory
  type: project
  originSessionId: 194e861a-1600-47ab-8797-26e7d6776f8f
  modified: 2026-08-18T19:59:42.651Z
---

**Named "V3 Sentinel"** (2026-08-17, user asked for "V3" + whatever name
fit — chosen for the system's core job: constant watch over price
against structure, alerting the moment it matters). Use this name going
forward for the whole system (Data Bridge, Signal Engine, Execution
Bridge, all six Managers).

**Hard boundary, explicitly stated by the user**: never watch/screenshot
MT5 terminal windows, charts, or indicators — MT5 access is
**Python-API-only** (`MetaTrader5` package calls like
`symbol_info_tick()`), same as every existing `algo_v2*` bot already
does. This is different from the TradingView side, where screenshotting
tv_scraper's browser panes for diagnostics is fine and already an
established pattern (see [[project_tv_scraper_window_maximized]]) — the
MT5-no-screenshot rule is specific to MT5, not a blanket ban on visual
diagnostics everywhere.

**Alert Manager (`v3/alert_manager/`) is built** — see its own docstrings
for the current implementation. Two separate processes, both need to be
running:
- `python -m v3.alert_manager.watcher` — push alerts (MT5 live price
  entering a virgin tv_scraper zone). M1/M3 timeframes excluded by
  default (`ALERT_MANAGER_EXCLUDED_TIMEFRAMES`, user's explicit request).
- `python -m v3.alert_manager.telegram_commands` — on-demand pull:
  replies to a `/bias <SYMBOL>` (or bare `/bias` for all three) Telegram
  message with a condensed current summary. Deliberately a SEPARATE
  process from watcher.py so a slow reply can't delay a push alert.
  Does NOT apply the M1/M3 exclusion (that filter is for unsolicited
  push alerts only, not an on-demand pull the user explicitly asked
  for) -- confirm with the user before changing this if asked to make
  the two consistent.

No MQL5/EA involved for either Telegram path; MT5 mobile push, if built
later, is the part that would need MQL5-side `SendNotification()`.

**Two reliability fixes landed 2026-08-17** after the user reported a
false-positive alert (XAUUSD M5 bear zone fired, nothing matching on the
actual chart):
1. `ZoneStore.TVZone.formed_time_confirmed` (new field) — False when a
   zone's start_time is a wall-clock guess because Pine's `[]` operator
   hit its hard 10000-bar lookback ceiling (~35 days on M5), not a real
   Pine-confirmed formation time. Such zones can be genuinely very old
   and already retested/mitigated in reality despite looking "just
   formed" -- Alert Manager now skips them entirely.
2. `v3/alert_manager/confirmation_tracker.py` (new) — requires a zone to
   be virgin across 2 DISTINCT tv_scraper writes (detected via the zone
   file's own mtime) before Alert Manager trusts it, closing the gap
   between MT5's 1s live-price checks and tv_scraper's much slower
   (20-60s+, worse with all 3 symbols running) refresh + its own
   2-consecutive-miss deletion debounce.

User's explicit call on the underlying trade-off: **keep MT5 as the
alert trigger regardless of remaining MT5-vs-TradingView price
divergence** ("its fast, if we have a gap of price between Tv and Mt5,
we deal with it later") -- these two fixes close the *data-quality*
false-positive class, not the inherent broker-price-divergence one,
which remains an accepted, known trade-off.

Also confirmed live: tv_scraper's real per-timeframe refresh rate with
all 3 symbols running concurrently is **~20-60 seconds**, not the
configured 5s poll_seconds (that's only the pause between sweeps; each
pane's own click+scroll+read adds real time, multiplying across 6-8
panes per symbol × 3 symbols competing for the same CPU). User was
offered several speedup options (lower poll_seconds, cut the per-pane
safety delays that fixed earlier misread bugs, reduce XAUUSD to 6
timeframes) and explicitly deferred: "lets see later."

Agreed 2026-08-17, before any Signal Engine / Execution Bridge code
exists yet -- this is the target shape to build toward, not something
already implemented (only Data Bridge is built at the time this was
agreed; Trend Manager inside Signal Engine is now built too -- see its
own section below). See [[project_tv_scraper_multi_symbol_setup]] for
the Data Bridge's current concrete state.

**Layers, in order:**
1. **Data Bridge** (built) — `v3/tv_scraper/` (pull, polling) + a future
   webhook receiver (push, instant retest alerts, `tv_bridge`-style, not
   built yet for crypto). Both write into the shared State Store
   (`ZoneStore`/`AtrStore` in `v3/tradingview_bot/`). Only job: get real
   chart facts in. Never decides anything, never touches MT5.
2. **Signal Engine** — reads the State Store, decides *whether/what* to
   trade. No MT5 order touched at this layer. Trend Manager (see its
   own section below) is built here; Reversal Manager and No Trade
   Manager are not.
3. **Execution Bridge** (was briefly called "Execution Pane" before
   user confirmed the "Bridge" naming to match Data Bridge's symmetry)
   — takes Signal Engine's decision, manages the actual MT5 order
   lifecycle. Core reconciliation loop built 2026-08-18 (see its own
   section below) — places/cancels/replaces real orders off Trend
   Manager's decisions, trading disabled by default. Stoploss
   Manager/Exit Manager (the two sub-components meant to live inside
   this layer) are NOT built yet — no trailing SL, no independent exit
   decision beyond what Trend Manager's own mitigation/flip/manual-
   cancel closes already cover.

**The six named Managers, classified by which layer owns them** (user's
original list was flat; this split was proposed and confirmed):

*Inside Signal Engine* (deciding, no order touched):
- **Trend Manager** — built and running, see its own full section below.
- **Reversal Manager** — detects when the opposite side is now winning.
  **Has two consumers, not one**: feeds both Signal Engine's own entry
  decision AND Execution Bridge's Exit Manager (force-closing an
  already-open opposite position, matching the old `algo_v2/main.py`
  pattern of squaring off on an opposite ELIGIBLE winner) — design as
  one shared signal both sides subscribe to, not two independent
  reversal checks that could disagree. **Has its own separate magic
  number from Trend Manager** (confirmed 2026-08-17) — the two Managers
  can independently hold same- or opposite-direction positions, so they
  must never share one. Not built yet -- until it exists, nothing in the
  system reacts to a reversal against an open Trend Manager trade at
  all.
- **No Trade Manager** — NOT the generic news/spread/weekend gate
  originally guessed. Actual rule, given 2026-08-17: watches HTF zones
  (H4/H2/H1/M30/M15, both symbols) — if price is inside or within a
  buffer of an HTF zone that would REJECT a would-be new trade (e.g. an
  H1 BEAR zone means "no long buffer": don't open new BUYS while price
  is at/near it, since that level is expected to push price down;
  symmetric "no short buffer" for an HTF BULL zone), new entries in
  that direction are suppressed. Does NOT close/affect an existing
  trade already in profit — only blocks new initiation. Buffer width
  (how far outside the zone's own `[btm, top]` this extends) was never
  pinned down — user's own example had price already below the zone's
  edge, so it's clearly wider than the zone itself, but by how much is
  unresolved. **Deferred** ("we can set later and we can integrate
  later") — not built, not integrated into Trend Manager's entry logic
  yet, so right now Trend Manager will happily fire an entry directly
  into HTF resistance/support with no such gate at all.

*Inside Execution Bridge* (managing a position already open or about to open):
- **Stoploss Manager** — initial SL placement at entry (Trend Manager's
  own `entries.select_sl` already computes this, Execution Bridge sends
  it with the order) + trailing afterward (trailing NOT built — user's
  own note: "trail sl to the nearest ob from current price," Stoploss
  Manager's job specifically, still pending).
- **Exit Manager** — decides when to close. Partially covered already
  by Trend Manager's own three close paths (OB mitigation, opposite-
  parent bias flip, manual cancel/close relayed from Execution Bridge)
  — a dedicated Exit Manager with more sophisticated logic is still
  not built.

*Beside both, independent* (not part of the Signal->Execution chain at all):
- **Alert Manager** — Telegram (+ later MT5 mobile push) notifications.
  Pure observer: reads the State Store + live MT5 tick price
  independently, never gates or feeds into Signal Engine or Execution
  Bridge. Deliberately decoupled so it can't slow down or break the
  trading path, and keeps working even if Signal Engine has a bug. See
  [[project_virgin_zone_telegram_alerts]] for the credential/pattern
  reuse plan (old `algo/alerts.py` code is deleted but the pattern and
  Telegram bot credentials are documented there).

**Scope decision**: every Manager is **one symbol-agnostic instance
handling all symbols in a single loop** (BTCUSD/XAUUSD/ETHUSD now, more
crypto later) — explicitly NOT one instance per symbol. Matches the
existing `algo_v2_usoil_btc_eth` pattern (one merged process looping
over symbols) rather than the older one-bot-per-symbol pattern
(`algo_v2_fx` loops per-symbol too, `algo_v2_tv_xauusd` is currently
single-symbol only because it predates this decision).

**Alert Manager build details already scoped** (from the same
conversation, ready to build): connects once to MT5 (`MetaTrader5-5`
terminal — same one `algo_v2`/`algo_v2_usoil_btc_eth` already use, plain
symbol names `XAUUSD`/`BTCUSD`/`ETHUSD`, no broker suffix) for live tick
prices; reads all three tv_scraper zone stores
(`tv_scraper_zones.json`/`tv_scraper_xauusd_zones.json`/`tv_scraper_ethusd_zones.json`)
each cycle for zones still `virgin`; on MT5 price entering a virgin
zone's `[btm, top]` for the first time, sends one Telegram message and
persists that zone as alerted (so a restart doesn't re-fire) — mirrors
the old `AlertedZoneStore` pattern. Deliberately uses MT5's live tick
feed rather than tv_scraper's own 5s-polled `retested` flag for lower
latency (user's explicit choice) — accepting that MT5's broker price can
differ slightly from the TradingView chart's own price.

---

## Trend Manager (`v3/signal_engine/`) — built, running, signal-only

Built 2026-08-17. Reserved magic number `26081701`
(`TREND_MANAGER_MAGIC_NUMBER`) -- not live yet, nothing here touches
MT5 today; every decision only ever logs `TRADE SIGNAL ...`. Runs
continuously (`python -m v3.signal_engine.trend_manager`,
`v3/trend_manager_run.log`) across XAUUSD/BTCUSD/ETHUSD.

**Structure / Short term reporting** (`compute()` in trend_manager.py):
Structure = direction of whichever M15 OB (bull or bear) formed most
recently for that symbol. Short term = same, but M5. Reported plainly,
always both fields, no blended bias and no derived Strong/Weak label —
explicit user call: agreement/disagreement is visible from the two raw
values themselves. Only counts `formed_time_confirmed=True` zones (same
trust bar as Alert Manager).

**Trade-initiation state machine** (`trade_tracker.py` +
`_run_trade_logic` in trend_manager.py) — one active trade per symbol
at a time:
- Two **parent timeframes** per symbol, each with its own permanent
  per-direction watermark (`TradeTracker`) — never trades an OB at or
  older than the newest one already traded/blocked in that bucket, even
  if it reappears looking fresh later (closes the exact top-4
  visibility-churn gap behind several Alert Manager false positives).
  XAUUSD: M5/M15. BTCUSD/ETHUSD: M15/M30 (crypto's own tv_scraper grid
  only covers H4/H2/H1/M30/M15/M5, no M1/M3 at all, so XAUUSD's scheme
  doesn't apply and everything shifts one tier up).
- Whichever parent has the newer eligible OB wins bias + direction
  ("M5 or M15, whichever is recent, wins").
- Separate **trigger timeframes** (never individually watermarked,
  pure execution triggers) must show an actual OB in the parent's
  direction before the parent fires. XAUUSD: M5/M3/M1. BTCUSD/ETHUSD:
  M15/M5.
- While a trade is open, any NEW same-direction OB on EITHER parent
  timeframe gets marked traded too (no pyramiding). Opposite-direction
  OBs anywhere are left alone entirely — Reversal Manager's job, not
  built.
- Trade closes when its own parent OB gets mitigated (removed from the
  Data Bridge's zone store) — current stand-in for "stopped out" until
  Execution Bridge tracks a real MT5 position.

**Entry-execution design — agreed 2026-08-17, NOT YET BUILT** (still
signal-only; this is the target for whenever Execution Bridge exists).
Adapts `algo_v2/entries.py`'s three-timeframe mechanism with real
changes, not a straight port:

- Once Trend Manager sets bias (parent OB decision above), all three
  execution timeframes (M1/M3/M5 for XAUUSD) start watching for entries
  in that direction only.
- **Post-parent-formation gate, applies to M1 AND M3 AND M5**: each one
  only reacts to an OB on its own timeframe that formed *after* the
  parent OB's own formation time — not any OB that already happens to
  exist. Confirmed explicitly for M3; M5 framing ("once m5 is formed")
  implies the same.
- **M1 — new thresholds**, not v2's old M1 logic (which was a pure
  edge+buffer pending, never market/pullback). Distance = price at OB
  formation minus OB edge: `<3` → market order. `3–6` → pending at 45%
  pullback, offset-from-edge floored at a static `3` (mirrors v2's
  `PULLBACK_MIN_EDGE_OFFSET`, same shape, scaled down from 4 to 3).
  `>6` → this M1 OB doesn't trade; wait for a newer M1 OB, or fall
  through to M3/M5.
- **M3 and M5 — unchanged**, reuse `algo_v2/entries.py`'s existing
  `m3_entry()`/`m5_entry()` exactly (market≤4, pullback 4–12, 45%,
  floor 4).
- **Continuous best-setup selection, not one-shot**: if M5 places a
  pending order and a fresh M3 (or M1) OB later appears with a *better*
  setup (market beats pending; closer beats farther — mirrors v2's
  `choose_winning_candidate`), the better one takes over: cancel the
  old order, place the new one. Trend Manager itself owns this
  comparison and decides which timeframe gets permission to enter —
  not a separate component.
- **Stop-vs-market fallback**: pending entries use BUY STOP/SELL STOP.
  If price has *already* moved into the target range by the time a
  pending order would be placed (a stop would just trigger instantly
  anyway), send a market order directly instead — stop-type pending
  orders are dropped in favor of this fallback for that specific case
  only, not removed from the mechanism generally.
- **Blocking on fill vs cancel — real fix over v2's existing bug**:
  - Order actually **fills** → permanently block that specific
    trigger-timeframe OB's own bucket, **and** the parent OB's own
    bucket too (propagates up) — this is the fix: v2's existing logic
    only blocks the ONE timeframe that got used, so a cancelled/losing
    setup on a DIFFERENT timeframe can immediately grab the same
    underlying opportunity right after. Blocking the parent too closes
    that gap.
  - User **manually cancels** a pending order → same full block as a
    fill (parent + that trigger timeframe) — signals "not interested in
    this OB," must not let a different timeframe immediately step in
    and take it anyway.
  - System cancels-and-replaces a pending order because a *better*
    setup appeared → NOT blocked, that OB stays eligible (mirrors
    `algo_v2`'s own bot-cancel vs manual-cancel distinction in
    `intervention.py`'s `expected_cancellations` set — same pattern
    will need reusing here once real orders exist).
  - Any of these blocks are **permanent**: never released by
    mitigation/invalidation of that OB, or of anything older in its
    bucket. Only ever superseded by a genuinely NEWER OB forming on
    that same bucket.
- **Bias flip, built into Trend Manager itself** (not the separate,
  deferred Reversal Manager) — "opposite ob on m5 or m15 will surely
  flip the bias" (user, 2026-08-17): an eligible, opposite-direction
  parent OB appearing on EITHER parent timeframe closes and permanently
  blocks the current trade (pending or filled), same treatment as a
  manual cancel, and the opposite side becomes the new active bias, all
  within the same poll cycle. Deliberately simple/mechanical — the real
  future Reversal Manager may do something more sophisticated and
  potentially supersede this, but this basic version stays in Trend
  Manager for now.
- **XAUUSD-only while being validated, then extended to all three
  2026-08-17** ("okay extend this to BTCUSD and ETHUSD") — no longer
  scoped down; all three symbols run today with their own parent/
  trigger timeframe sets (see the table above).

## Execution Bridge (`v3/execution_bridge/`) — built 2026-08-18, disabled by default

Reconciles real MT5 pending orders/positions against whatever Trend
Manager has already decided (reads `trend_manager_trade_state.json`
read-only — same interchange-file pattern used everywhere in this
system; never writes to it). Never decides direction/entry/SL itself.

**`EXECUTION_BRIDGE_ENABLE_TRADING` defaults `false`**, same convention
as every bot in this repo — every `order_send`/cancel/close call is
gated behind it; with it false, every decision only ever prints "WOULD
place/cancel/close ...". Reuses Trend Manager's own magic number
(`26081701`) — every order this places IS a Trend Manager order, not a
separately-identified one. Building this did NOT enable live trading —
flipping that flag needs the user's explicit go-ahead, confirmed fresh
each time, never assumed from a prior approval (see this project's
saved collaboration notes and CLAUDE.md).

**LIMIT orders for pending entries**, confirmed explicitly by the user
2026-08-17 — the retracement math always puts the entry between the OB
edge and current price (behind current price), which is LIMIT-order
behavior. STOP only ever fires as an automatic defensive fallback
(mirrors `algo_v2/broker.py`'s own order-type auto-selection) if price
has already passed the entry by send time — should be rare/never given
`entries.py`'s market-order fallback already covers that case earlier.

**What it does each cycle, per symbol**:
1. Checks whether a previously-tracked real ticket has disappeared from
   MT5 since last poll. If so, works out why via `intervention.py` (v3's
   own copy of `algo_v2/intervention.py`'s manual-vs-bot detection, not
   an import) — filled, manually cancelled/closed (relays this to Trend
   Manager, see below), SL/TP hit, or Execution Bridge's own expected
   cancellation (tracked so a self-initiated cancel-and-replace is never
   mistaken for manual).
2. Reconciles against Trend Manager's desired state: nothing desired but
   something's tracked → cancel/close it; PENDING desired but the
   tracked ticket doesn't match (Trend Manager replaced the proposal) →
   cancel old, place new; PENDING desired, nothing tracked → place it;
   FILLED+MARKET desired, nothing tracked → place a market order now;
   FILLED+PENDING desired (Trend Manager's own price-crossed simulation
   says reached) → look for the real position the broker should already
   have filled it into on its own, start tracking it once found.

**Manual-cancel feedback loop, the whole reason this needed building**:
`manual_events.py` is a small read-only relay — Execution Bridge writes
a timestamp there the instant it detects a REAL manual cancel/close in
MT5; `v3/signal_engine/trade_tracker.py`'s new
`should_react_to_manual_event` (idempotent, per-symbol watermark on the
event timestamp) consumes it and applies the exact same permanent block
a bias flip already gets. Before this existed, "a cancel is basically
blocking the trade" (user's rule, same day) had nothing real to act on
— Trend Manager could only ever simulate fills via price-crossing, never
observe an actual manual intervention.

Verified in dry run (`EXECUTION_BRIDGE_ENABLE_TRADING=false`) against
live Trend Manager state: correctly reconciled real (absent) MT5 state
against XAUUSD/ETHUSD both showing FILLED bear trades and BTCUSD
showing none, printing the right WOULD-place lines for each without
touching MT5.

## Stoploss Manager (`v3/execution_bridge/stoploss_manager.py`) — built 2026-08-18

Point-based SL trailing, user's rule verbatim (explicitly supersedes an
earlier, vaguer "trail to nearest OB" idea — **no OB-based trailing at
all** anymore):
- Below **breakeven_points** (default 7) favorable movement: SL stays
  at its initial value, untouched.
- >= breakeven_points: SL moves to cost (entry price).
- >= **trail_start_points** (default 10): trails in **trail_step_points**
  (default 2) increments from there — e.g. +12 points favor → SL at
  entry+2. Based on the PEAK favorable move ever reached for the
  position, not instantaneous profit, so it only ever ratchets tighter,
  never loosens on a partial retrace.
- **Manual SL changes in MT5 are respected** — trailing pauses the
  moment the real SL no longer matches what Stoploss Manager itself
  last set, and doesn't resume until price makes a genuinely NEW high
  (buy) / new low (sell) beyond where price was at the moment of that
  change.

Thresholds are per-symbol-configurable — **resolved 2026-08-18**, no
longer XAUUSD-scaled placeholders. User gave exact values for both:
- XAUUSD: 7/10/2 (two-stage — a wider dead zone between breakeven and
  trail start).
- ETHUSD: 15/15/5 (single-stage — one threshold both moves SL to cost
  AND starts stepping, `breakeven_points == trail_start_points`).
- BTCUSD: 300/300/150 (same single-stage shape).
`_desired_sl`'s existing formula already handled `breakeven ==
trail_start` correctly with no code change — only new config values
were needed. Also pulled real per-symbol entry/SL buffers from
`algo_v2_usoil_btc_eth/entries.py` (BTCUSD sl_buffer 20.0, market≤175,
pullback 175–600; ETHUSD sl_buffer 2.0, market≤4, pullback 4–20 —
`entries.py` restructured around a `(symbol, timeframe)` → `EntryConfig`
lookup for this). Caught a real latent bug in the same pass: M15
triggers had NEVER been able to fire for BTC/ETH at all (the old
`ENTRY_FUNCS` map only ever had "1"/"3"/"5", never "15", so crypto's
own M15 trigger silently no-opped since this system was first built) —
fixed by the same per-`(symbol, timeframe)` restructure.

**Initial SL bug fixed the same day**: the original `entries.select_sl`
(cross-timeframe "closest edge" search) had SL using the SAME edge as
entry (a bull OB's top) instead of the OPPOSITE edge (its bottom) —
backwards from `algo_v2`'s own actual convention. Replaced with
`entries.initial_sl()`: SL is now based only on the OB the trade
actually executed off, using its opposite edge, buffered by 1.0 (raised
from 0.5, user's explicit value) — simpler than the old cross-timeframe
search, and correct.

Verified: `initial_sl`'s edge direction/buffer directly, and the full
trailing formula against the user's own worked example (+12 points
favor → SL at entry+2, both directions) via a standalone unit check. No
open position existed at build time to exercise the live end-to-end
path against a real position — that remains unverified against reality
until a real trade is open.

## Live trading enabled 2026-08-18 (demo account only)

`EXECUTION_BRIDGE_ENABLE_TRADING=true`, on explicit user go-ahead after
confirming the account: Exness Trial server, login 83111022, demo (no
real money). Execution Bridge and Trend Manager both run continuously
live. A manual test order (0.01 XAUUSD, open then immediate close) was
sent and verified end-to-end: filled at 4367.023, closed at 4366.568,
P/L -$0.45, zero positions/orders left open anywhere on the account
afterward, no other bot's magic number or state touched. **This is
still demo-only** — going to a real-money account needs its own
fresh explicit go-ahead, never inferred from this one.

Lot sizes set to match the account's other existing bots (not
placeholder defaults): XAUUSD 0.04, BTCUSD 0.05, ETHUSD 1.0.

## Reversal Manager (`v3/signal_engine/reversal_manager.py`) — built 2026-08-18, signal-only

Separate component from Trend Manager — own magic number (`26081801`,
distinct from Trend Manager's `26081701`), confirmed 2026-08-17 that
the two can hold a same- or opposite-direction position on the same
symbol simultaneously. Not wired to Execution Bridge yet — that
integration is a deliberate separate step, not done as part of this
build.

**Two entirely different mechanisms depending on timeframe:**
- **M5's own retest** → fires a market order immediately, no waiting.
  SL = `entries.initial_sl` (reused as-is from Trend Manager). Stoploss
  Manager's existing point-based trailing takes over from there —
  reused wholesale, not rebuilt for Reversal Manager.
- **H4/H2/H1/M30/M15 retest** → does NOT enter immediately. Registers a
  "waiting" retest and starts watching M1/M3/M5 for a FRESH
  same-direction OB (formed strictly after the retest time) to
  confirm. M1/M3/M5 never trigger a reversal trade independently —
  only as confirmation for an active HTF wait.
  - M1 uses its own WIDER confirmation thresholds (market≤4, pullback
    4<d<8) — deliberately different from Trend Manager's own M1
    (3/3-6): "we might catch a bottom or top... keep some space
    buffer, making sure not missing the entry" (user's reasoning).
  - M3/M5 reuse Trend Manager's own `m3_entry`/`m5_entry` unchanged
    ("already prescribed entry logics").
  - Whichever LTF confirms first wins (MARKET beats PENDING, closer
    beats farther — same selection shape as Trend Manager's own).
- **Invalidation**: an opposite-direction LTF OB forming while waiting
  scraps the whole setup — blocked until the NEXT retest re-arms it
  (the retest that started the wait is already permanently
  watermarked, so only a genuinely newer retest can start a fresh one).
- **SL with multiple zones waiting at once**: "if a single candle
  retests multiple zones, whichever zone is at lowerside for buy trade
  decides sl" — furthest btm (buy) / furthest top (sell) among ALL
  zones currently in the waiting list, computed at the moment
  confirmation actually fires, not just the one that started the wait.
- One reversal trade per symbol at a time (mirrors Trend Manager's own
  rule), though Reversal Manager as a whole can still disagree with
  Trend Manager's concurrent position.

**Real bug caught and fixed during testing, before this was trusted**:
the first test run fired "reversal trades" on all three symbols off an
M5 zone where `retested_at` exactly equalled `start_time` — a known
`tv_scraper` artifact (a reused Pine array slot inheriting an old
zone's Retested flag on first sighting, before genuinely being
touched) that `tv_scraper` already guards ONE path against but not
every path. Fixed by having Reversal Manager itself distrust
`retested_at == start_time` directly rather than assuming the upstream
guard always caught it — same defensive spirit as `formed_time_confirmed`
used everywhere else in this system. Re-verified after the fix against
live data: legitimate retests (genuinely different formed/retested
timestamps) still fire correctly, and the false-positive pattern is
now suppressed.

LTF timeframe sets differ per symbol like Trend Manager's own trigger
timeframes: XAUUSD has M1/M3/M5; BTCUSD/ETHUSD only have M5 (no M1/M3
data at all in their own tv_scraper grid) — HTF set (H4/H2/H1/M30/M15)
is identical across all three symbols, unlike Trend Manager's own
per-symbol parent timeframes.

## Reversal Manager wired to Execution Bridge — 2026-08-18

Execution Bridge now reconciles TWO independent sources every cycle:
Trend Manager (magic `26081701`) and Reversal Manager (magic
`26081801`), each with its own order tracking + SL trailing state file
(`config.py`'s `SourceConfig`), each able to hold a position on the
same symbol at the same time. Reversal Manager's own state gained a
`status` field (AWAITING_TRIGGER/PENDING/FILLED) + fill-on-price-cross
tracking it was missing entirely before this — without it Execution
Bridge couldn't tell "already filled" from "never placed" for a
Reversal Manager pending trade.

**Three real bugs caught and fixed while wiring this live** (demo
account, `EXECUTION_BRIDGE_ENABLE_TRADING=true`, all found from actual
production behavior, not from testing):

1. **Comment-length bug** — `close_position`'s comment embedded the
   full free-text reason (47 chars), MT5 rejects anything over ~31
   chars with "Invalid comment", so the close silently failed.
2. **Tracker-clear-on-failure bug** — the close-failure above ALSO
   made Execution Bridge forget it was tracking the position at all
   (cleared unconditionally, even on failure), so nothing ever
   retried. Combined with bug 1, this left a real XAUUSD position
   **stuck open** until caught and fixed live — had to manually
   re-register the ticket in `execution_bridge_orders.json` so the
   fixed code would retry the close.
3. **Direction-encoding bug** — comments truncated direction to one
   letter, but `"bull"[0]` and `"bear"[0]` are both `"b"` — could never
   actually distinguish them (harmless so far, unused by any caller,
   but fixed before it mattered). Comments now spell out `bull`/`bear`
   in full.

**Real architectural gap also closed**, found via a live SL hit on
BTCUSD: Trend Manager's own state had **no way to learn a real
position had been stopped out** — its only closure signal was the OB
itself getting mitigated on the chart, which is unrelated to a real
SL hit. `manual_events.py`'s relay (renamed `should_react_to_manual_event`
→ `should_react_to_close_event`, `_check_manual_event` →
`_check_close_event`) now covers SL/TP hits alongside manual
cancels/closes — all three mean the same thing to the source Manager:
the trade is over in reality, close and permanently block it. Before
this fix, a stopped-out BTCUSD trade left Trend Manager's own state
showing `FILLED` indefinitely with nothing real behind it, and
Execution Bridge just logged "no matching real position found yet"
forever without ever resolving it.

Verified live end-to-end after all fixes: the stuck XAUUSD position
closed successfully; Reversal Manager's M5-immediate rule fired real
market orders for BTCUSD and ETHUSD moments after restart, both
correctly tracked under Reversal Manager's own magic number/comment
prefix, fully independent of Trend Manager's own tracking for the same
symbols. All three processes (`trend_manager`, `reversal_manager`,
`execution_bridge`) now run continuously together.

**That "wired live" moment immediately surfaced two MORE real bugs**,
both fixed same session:

1. **Cold-start fired on stale retests, twice** — the first Reversal
   Manager restart fired real trades off a day-old BTCUSD retest and
   an already-stale ETHUSD one, simply because a brand-new watermark
   treats whatever's currently already-retested as "new." A whole-file
   "was this the first run" flag fixed THAT case, but the very next
   restart fired on a real but WEEK-old BTCUSD/ETHUSD retest on the
   **bear** direction specifically — bear had simply never been active
   before (only bull had fired), so it looked like a fresh cold start
   even though the file already existed. Real fix: `ReversalTracker`'s
   `seeded_buckets` set tracks readiness **per
   (symbol, timeframe, direction) bucket**, not per file — the first
   time ANY bucket is ever examined, its watermark seeds to whatever's
   currently retested (skipped, not fired), and only a retest
   happening strictly after that first look ever counts. This is the
   general, self-healing fix — a bucket that becomes newly relevant
   later (new symbol, new timeframe) will always self-seed correctly
   on its own first look, no more special-casing needed.
2. **Reversal Manager had no SL/TP-hit relay at all** (Trend Manager
   got this fix earlier in the session; Reversal Manager needed the
   identical treatment once IT went live). A real SL hit on an
   untracked BTCUSD position left Reversal Manager's own state showing
   `FILLED` forever, and Execution Bridge kept re-opening a brand new
   position for it every single cycle — confirmed live, this actually
   happened repeatedly before being caught. Fixed with the same
   `should_react_to_close_event` pattern, its own dedicated
   `EXECUTION_BRIDGE_REVERSAL_MANUAL_EVENTS_FILE` relay file (separate
   from Trend Manager's).

Also fixed a misleading log line along the way: Execution Bridge
claimed "notified {source}" even when that source had no
`manual_events_file` configured at all (nothing was actually written).

All stale positions closed manually, both Managers' own state cleared
to match, then verified clean on restart: every previously-untouched
bucket correctly logged "first look... skipping pre-existing retest"
instead of firing, and the account sat at zero open positions/orders
with a quiet log afterward.

**Takeaway worth remembering**: going from signal-only to genuinely
live trading surfaced real bugs that no amount of dry-run testing
caught, because they only manifest against real MT5 state, real
restarts, and real timing — comment-length limits, tracker state
surviving failures, direction-encoding collisions, cold-start
watermark semantics, and cross-Manager close-event propagation all
only broke in actual live conditions. Expect this pattern to repeat
if/when this ever moves toward a real-money account — budget for a
similar live-debugging pass, don't assume dry-run-clean means
live-clean.

## Third instance of the stale-retest pattern — fixed 2026-08-19

A real XAUUSD trade fired via Reversal Manager's M5-immediate path on
a zone whose own `retested_at` was **12 days old**. Both prior guards
(`retested_at != start_time`, per-bucket seeding) correctly let it
through, since both only check RELATIVE ordering ("is this newer than
what we've seen") — neither can catch a zone that simply wasn't
visible in tv_scraper's own top-4 at first-look time (so the seed
never captured it), then reappeared later carrying its original
ancient timestamp, which still looks "newer than anything processed"
to a pure watermark.

Fix: `_newest_retested_zone` now also requires `retested_at` to be
within **30 minutes** (`_RETEST_MAX_AGE_SECONDS`) of wall-clock now —
an absolute recency check the ordering-based watermark can never
provide on its own. This is the general fix for the whole pattern
(exact-timestamp artifact → per-bucket cold start → absolute
staleness) — three layers, each catching a different failure mode of
"data that LOOKS fresh to the tracker but isn't fresh in reality."

Stale XAUUSD position closed (opened @ 4346.01, SL 4244.86, real
money — well, demo money — at risk for a while before caught). The
two other open positions (BTCUSD, ETHUSD) were independently confirmed
fresh already and left untouched. Also worth noting for future
debugging: nearly triggered a false alarm here by misreading a STALE
LOG LINE (from an earlier restart much earlier in the session) as a
live re-fire — always verify against real MT5 state and a direct
function call against live data before trusting a log tail, especially
on a long-running file that's been through many restarts.
