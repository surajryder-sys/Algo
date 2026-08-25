"""Trend Manager -- the first Manager built inside Signal Engine (see
v3/signal_engine/__init__.py and the project_v3_crypto_architecture
memory note). Decides Structure and Short-term per symbol from the Data
Bridge's own OB zone data (v3/tradingview_bot/zone_store.py) -- M15 and
M5 respectively, for every symbol (this reporting pair doesn't change
per symbol, only the trade-initiation timeframes below do). No blended
"bias" or "strong/weak" label -- by explicit user decision, the two
readings are reported plainly as-is; agreement or disagreement is
visible from the two values themselves, not a separately computed
field.

Rule (user's final, confirmed 2026-08-17):
- Structure = the direction (bullish/bearish) of whichever M15 OB
  (bull or bear) formed most recently for that symbol.
- Short term = the same, but for M5.
Only counts zones with formed_time_confirmed=True (see
ZoneStore.TVZone's own docstring) -- a zone whose start_time is a
wall-clock guess rather than a real Pine-confirmed formation time can't
be trusted to actually BE the most recent OB; same reasoning Alert
Manager already applies before treating a zone as real.

=== Trade initiation + entry execution (2026-08-17, user's rules) ===
See trade_tracker.py for the persisted state this enforces, and
entries.py for the entry-price/SL math (v3's own copy of algo_v2's
shape, not an import -- see that module's docstring).

--- Bias (parent OB) ---
Two "parent" timeframes per symbol (SymbolConfig.parent_timeframes),
each with its own permanent per-direction watermark. Whichever parent
has the newer eligible OB wins bias + direction ("whichever is recent,
wins"). XAUUSD: M5/M15. BTCUSD/ETHUSD: M15/M30 (their own tv_scraper
grid has no M1/M3 at all -- see project_tv_scraper_multi_symbol_setup
-- so XAUUSD's scheme doesn't apply and everything shifts one tier up).

While a trade is open (pending or filled), any NEW same-direction OB on
EITHER parent timeframe gets marked traded too (no pyramiding). An
OPPOSITE-direction, eligible parent OB appearing on EITHER parent
timeframe FLIPS the bias -- closes and permanently blocks the current
trade (same treatment as a manual cancel), and the opposite side
becomes the new active bias. This is deliberately simple/mechanical
(not the future Reversal Manager's job -- that's a separate, more
sophisticated component the user is deferring; this is just "opposite
parent OB shows up -> get out," confirmed in-scope for Trend Manager
itself 2026-08-17).

Corrected 2026-08-25: the opposite OB must be genuinely NEWER
(start_time strictly after the active trade's own parent start_time)
to flip -- not just "eligible." Real live incident: an M5 bull OB and
an M15 bear OB formed on the exact same candle-open (identical
start_time); M5 confirmed first and fired a trade, then M15 finished
confirming ~10 minutes later and, under the old rule, closed that
trade even though its OB was no newer -- just slower to confirm. A
same-age or older opposite OB no longer overrides an already-active
bias; only a strictly newer one does.

--- Entry execution, once bias is set ---
Trigger timeframes (SymbolConfig.trigger_timeframes) are pure execution
triggers -- XAUUSD: M5/M3/M1. BTCUSD/ETHUSD: M15/M3 (was M15/M5 until
2026-08-22, when the user changed both symbols' actual bottom chart
pane from M5 to M3 -- "change it to m3 everywhere"). Each only reacts
to an OB on its own timeframe that formed AFTER the parent OB's own
formation time -- not any OB that already exists (applies to all
trigger timeframes, confirmed explicitly for M3, implied for M5).

Distance is measured fresh each time a NEW post-parent trigger OB is
first seen, using that timeframe's own CURRENT close price (from
tv_scraper's live snapshot -- deliberately TradingView-sourced, not
MT5: "through tv scraper is best, mt5 only for placing orders and
getting live price," keeping Trend Manager MT5-free). M1 uses new
thresholds (market<=3, pullback 3<d<6, floor 3). M3/M5 reuse v2's old
thresholds unchanged (market<=4, pullback 4<d<12, floor 4). Once
computed, a PENDING entry's price is fixed -- not continuously
re-computed every cycle; it either fills (price reaches it) or gets
cancelled-and-replaced by a NEWER, BETTER candidate.

Best-setup selection is continuous: every cycle, all trigger timeframes
with a valid (non-NONE) plan off a genuinely newer post-parent OB are
compared -- MARKET (fills instantly, distance 0) always beats PENDING;
among the same mode, closer to current price wins (mirrors algo_v2's
own choose_winning_candidate). If the winner differs from whatever's
currently proposed, cancel-and-replace: NOT blocked, that superseded OB
stays eligible for later (mirrors algo_v2's own bot-cancel vs
manual-cancel split in intervention.py's expected_cancellations).

SL = the PARENT OB's own opposite edge (bull: its bottom; bear: its
top), buffered by that symbol's own sl_buffer -- NOT whichever trigger
timeframe (M1/M3/M5) actually executed the entry. Changed 2026-08-19,
user's explicit correction ("whoever opens the trade, they should
follow parent ob sl") -- the trigger OB only ever decided entry price/
timing, never SL, going forward. Previously (2026-08-18) this used the
executed trigger OB's own opposite edge instead; before that, an even
earlier "closest edge" cross-timeframe search (see
entries.initial_sl_from_parent vs the still-present entries.initial_sl,
which Reversal Manager's own M5-immediate case continues to use).

--- Blocking (permanent, never releases on mitigation) ---
An OB only gets permanently blocked (its own bucket's watermark
advanced) when: (a) its order actually FILLS (market fills instantly;
pending fills when price crosses the fixed entry price), (b) it's
flip-closed by an opposite parent OB, or (c) the user manually cancels
a pending order or manually closes an open position in MT5. Getting
cancelled-and-replaced by a BETTER system-chosen setup does NOT block
it -- that's the real fix over algo_v2's existing behavior, where only
the one timeframe that got used blocks, letting a different timeframe
immediately grab the same underlying opportunity right after a manual
cancel.

(c) is detected by v3/execution_bridge/'s own intervention.py (real MT5
history, not simulated) and relayed here read-only via
manual_events.py's small event file -- see
TradeTracker.should_react_to_close_event and this module's own
_check_close_event. Trend Manager never touches MT5 itself; it only
reads that one small file Execution Bridge writes.

Stop-vs-market fallback: if price has already moved into range by the
time a pending order would be placed, fire market instead of a stop
that would just trigger instantly anyway -- this IS what compute_entry
already does (distance <= market_max -> MARKET), no separate code path
needed.

Signal-only here -- this module only ever decides and logs.
v3/execution_bridge/ is what actually places/cancels/closes real MT5
orders off these decisions (own EXECUTION_BRIDGE_ENABLE_TRADING flag,
defaults false like every other bot in this repo).

--- M1 exit exception (XAUUSD only, added 2026-08-20) ---
An M5- or M3-triggered trade still closes on mitigation of its own
reference OB, as above. An M1-triggered trade does NOT -- M1's own OB
invalidation carries too much noise, per the user's explicit rule.
Instead it closes on whichever comes first: a fresh opposite-direction
OB forming on M1, a fresh opposite-direction OB forming on M3, or the
M3 OB that was "in play" (newest eligible, post-parent, same
direction) at the moment the M1 trade fired getting mitigated. See
_close_if_m1_noise_exit's own docstring. The parent-level opposite-OB
bias-flip close above still applies on top of this, unchanged -- an
opposite M5 (or M15) parent OB always closes the trade regardless of
which trigger timeframe opened it.

Run with: python -m v3.signal_engine.trend_manager
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from v3.execution_bridge import manual_events
from v3.signal_engine import entries
from v3.signal_engine.config import Config, SymbolConfig, load_config
from v3.signal_engine.trade_tracker import ActiveTrade, TradeTracker
from v3.tradingview_bot.atr_store import AtrStore
from v3.tradingview_bot.zone_store import TVZone, ZoneStore

_M15 = "15"
_M5 = "5"

_DIRECTION_LABELS = {"bull": "bullish", "bear": "bearish"}
_TF_LABELS = {"240": "H4", "120": "H2", "60": "H1", "30": "M30", "15": "M15", "5": "M5", "3": "M3", "1": "M1"}


@dataclass(frozen=True)
class TrendReading:
    symbol: str
    structure: Optional[str]        # "bullish" / "bearish" / None (no M15 data yet)
    short_term: Optional[str]       # "bullish" / "bearish" / None (no data yet on short_term_timeframe)
    short_term_timeframe: str       # which timeframe short_term actually came from -- see compute()


def _formation_trusted(zone: TVZone) -> bool:
    """Whether this zone's own start_time can be trusted as a real
    formation time -- both currently confirmed AND never once seen
    unconfirmed. Added 2026-08-19, v3's signal_engine-wide copy of
    Reversal Manager's own helper of the same name (see that module's
    docstring for the live incident: formed_time_confirmed can flicker
    True on a single poll for a genuinely old zone due to ordinary
    scrape flakiness, which is enough to slip past a bare
    `zone.formed_time_confirmed` check). Replaces every such bare check
    in this module too, for the same reason -- Trend Manager's bias and
    trigger-OB selection are exactly as exposed to this as Reversal
    Manager's retest selection was."""
    return zone.formed_time_confirmed and not zone.formed_time_ever_unconfirmed


def _most_recent_direction(store: ZoneStore, symbol: str, timeframe: str) -> Optional[str]:
    """Most recent OB (bull or bear, whichever is younger) for this
    symbol/timeframe, by real Pine-confirmed start_time. None if there's
    no formed_time_confirmed zone on this timeframe at all yet."""
    best_start_time: Optional[int] = None
    best_direction: Optional[str] = None
    for direction in ("bull", "bear"):
        zones = store.zones(symbol, timeframe, direction)  # newest first
        for zone in zones:
            if not _formation_trusted(zone):
                continue
            if best_start_time is None or zone.start_time > best_start_time:
                best_start_time = zone.start_time
                best_direction = direction
            break  # zones() is newest-first -- first confirmed one is enough per direction
    if best_direction is None:
        return None
    return _DIRECTION_LABELS[best_direction]


def compute(store: ZoneStore, sym_cfg: SymbolConfig) -> TrendReading:
    # "Short term" reads M5 for every symbol by default (the original
    # 2026-08-17 rule) -- but USOIL/USTEC's shared scraper window has NO
    # M5 pane at all (H1/M30/M15/M3 only), so a fixed M5 lookup can only
    # ever return None for them, permanently, regardless of real market
    # activity. User's correction 2026-08-25: "we changed usoil and
    # ustec panes to m3 for executions" -- M3 is their real fast/
    # short-term timeframe (already tracked separately as
    # atr_confirm_timeframe, USOIL/USTEC's own M3-only execution
    # mechanism marker), so reuse that same field here rather than adding
    # a new one: when set, it doubles as "the short-term timeframe this
    # symbol actually has," falling back to M5 for every other symbol
    # (atr_confirm_timeframe is None for them, unchanged).
    short_term_timeframe = sym_cfg.atr_confirm_timeframe or _M5
    return TrendReading(
        symbol=sym_cfg.symbol,
        structure=_most_recent_direction(store, sym_cfg.symbol, _M15),
        short_term=_most_recent_direction(store, sym_cfg.symbol, short_term_timeframe),
        short_term_timeframe=short_term_timeframe,
    )


def _format_reading(reading: TrendReading) -> str:
    structure = reading.structure or "none"
    short_term = reading.short_term or "none"
    tf_label = _TF_LABELS.get(reading.short_term_timeframe, reading.short_term_timeframe)
    return f"{reading.symbol}: Structure {structure}, Short term ({tf_label}) {short_term}"


# ---------------------------------------------------------------------------
# Trade initiation + entry execution
# ---------------------------------------------------------------------------

def _newest_eligible_start_time(store: ZoneStore, tracker: TradeTracker, symbol: str,
                                 timeframe: str, direction: str) -> Optional[int]:
    """The newest formed_time_confirmed OB's start_time for this exact
    PARENT-timeframe bucket, but only if it's still eligible (newer than
    the bucket's own permanent watermark) -- None otherwise. zones() is
    newest-first, and a watermark only ever moves forward, so the first
    CONFIRMED zone found is authoritative: if it fails eligibility,
    every older zone in this bucket does too.

    Cold-start seeded, added 2026-08-19 after a real live incident: an
    M15 bearish OB over an hour old, never previously watermarked in
    that bucket, got treated as "the newest eligible parent" purely
    because start_time > 0 (the default watermark) -- flipping bias off
    a genuinely stale zone. v3's own copy of reversal_manager.py's
    identical fix: the FIRST time this exact bucket is ever examined,
    whatever's currently there gets seeded into the watermark instead of
    being treated as a fresh signal -- only a zone that appears AFTER
    that first look can ever set or flip bias from this bucket."""
    if not tracker.is_bucket_seeded(symbol, timeframe, direction):
        trusted = [z for z in store.zones(symbol, timeframe, direction) if _formation_trusted(z)]
        seed_start_time = max((z.start_time for z in trusted), default=0)
        tracker.seed_bucket(symbol, timeframe, direction, seed_start_time)
        if seed_start_time:
            tf_label = _TF_LABELS.get(timeframe, timeframe)
            print(f"[trend_manager] {symbol}: first look at {tf_label} {_DIRECTION_LABELS[direction]} parent -- "
                  f"skipping pre-existing OB @ {seed_start_time}, only reacting to new ones from here")
        return None

    for zone in store.zones(symbol, timeframe, direction):
        if not _formation_trusted(zone):
            continue
        if tracker.is_eligible(symbol, timeframe, direction, zone.start_time):
            return zone.start_time
        return None
    return None


def _best_parent_candidate(store: ZoneStore, tracker: TradeTracker, symbol: str,
                            parent_timeframes: Tuple[str, str],
                            directions: Tuple[str, ...]) -> Optional[Tuple[int, str, str]]:
    """(start_time, timeframe, direction) of the single newest eligible
    OB across the given parent timeframes and directions, or None."""
    best: Optional[Tuple[int, str, str]] = None
    for timeframe in parent_timeframes:
        for direction in directions:
            start_time = _newest_eligible_start_time(store, tracker, symbol, timeframe, direction)
            if start_time is not None and (best is None or start_time > best[0]):
                best = (start_time, timeframe, direction)
    return best


def _newest_post_parent_zone(store: ZoneStore, tracker: TradeTracker, symbol: str, timeframe: str,
                              direction: str, parent_start_time: int) -> Optional[TVZone]:
    """The newest formed_time_confirmed, still-eligible OB on this
    trigger bucket that formed AT OR AFTER the parent OB itself -- None
    if no such OB exists. Same newest-first short-circuit logic as
    _newest_eligible_start_time.

    Changed 2026-08-20 from a strict "AFTER" (>) to "at or after" (>=):
    for XAUUSD (M5) and BTCUSD/ETHUSD (M15), the parent timeframe is
    ALSO one of the symbol's own trigger timeframes, so the exact same
    OB that just won bias can legitimately BE the entry trigger too --
    user's explicit rule ("if a m5 forms it can surely buy, being a
    parent"). A strict > made that impossible by construction (an OB's
    start_time can never be greater than itself). Only actually changes
    behavior for that same-OB case: a genuinely distinct trigger OB on a
    different timeframe/candle can't retroactively equal parent_start_time
    in practice."""
    for zone in store.zones(symbol, timeframe, direction):
        if not _formation_trusted(zone):
            continue
        if zone.start_time < parent_start_time:
            return None  # newest confirmed zone isn't even at-or-after parent -> nothing older is either
        if not tracker.is_eligible(symbol, timeframe, direction, zone.start_time):
            return None  # newest confirmed+post-parent zone already blocked -> nothing newer/eligible exists
        return zone
    return None


def _newest_post_time_zone(store: ZoneStore, symbol: str, timeframe: str,
                            direction: str, after_time: int) -> Optional[TVZone]:
    """Newest confirmed OB formed strictly after after_time, no
    eligibility/watermark check -- v3's signal_engine-wide copy of
    reversal_manager's own identical helper. Used by
    _close_if_m1_noise_exit's opposite-OB checks below, where "is this
    trigger-eligible" doesn't matter -- any fresh OB in the right
    direction is a valid exit signal regardless of watermark state."""
    for zone in store.zones(symbol, timeframe, direction):
        if not _formation_trusted(zone):
            continue
        if zone.start_time > after_time:
            return zone
        return None
    return None


def _close_if_m1_noise_exit(store: ZoneStore, tracker: TradeTracker, symbol: str, trade: ActiveTrade) -> bool:
    """XAUUSD-only M1 exit rule -- user's explicit correction 2026-08-20:
    M1's own OB invalidation carries too much noise to close on, so
    this REPLACES close_if_invalidated's normal same-OB-mitigation
    check for an M1-triggered trade specifically (M5/M3-triggered
    trades are untouched, still use close_if_invalidated as before).
    Closes instead on whichever comes first:
    - a fresh opposite-direction OB forming on M3, OR
    - a SECOND distinct opposite-direction OB forming on M1 (raised from
      one to two, 2026-08-25 -- user's own correction: "m1 gets two
      bullish ob's in sequence after entering into the sell trade" -- a
      single M1 opposite OB proved too noise-prone by itself; M3's own
      single-OB trigger is untouched, see ActiveTrade.m1_opposite_ob_
      count's own docstring for why this needs persistent cross-cycle
      state rather than a bare "does one exist right now" check), OR
    - the M3 OB that was "in play" (newest eligible, post-parent, same
      direction) at the moment this M1 trade fired getting mitigated
      (trade.m3_watch_start_time, set once at fire time -- see
      _try_fire_entry).
    The existing parent-level opposite-OB bias-flip check in
    _run_trade_logic still applies on top of this, unchanged -- "if m5
    forms an opposite side ob, definitely it will close the trade, as
    opposite bias becomes active" is already exactly what that does."""
    opposite = "bear" if trade.direction == "bull" else "bull"
    if _newest_post_time_zone(store, symbol, "3", opposite, trade.exec_start_time) is not None:
        tracker.close_trade(symbol, block=True)
        return True

    # Count every DISTINCT opposite-direction M1 OB seen since entry, not
    # just "does one currently exist" -- a counted OB can later get
    # mitigated and vanish from the store, so this can't be re-derived
    # from the live store each cycle; it's tracked on the trade itself
    # (see record_m1_opposite_obs). zones() is newest-first, so this
    # collects every OB strictly newer than the last one already
    # counted, in case more than one formed within a single poll gap.
    since = trade.m1_opposite_ob_last_start_time
    if since is None or since < trade.exec_start_time:
        since = trade.exec_start_time
    new_sightings = [
        z for z in store.zones(symbol, "1", opposite)
        if _formation_trusted(z) and z.start_time > since
    ]
    if new_sightings:
        # Capture the count BEFORE record_m1_opposite_obs -- `trade` is
        # the SAME object tracker._active holds (active_trade() returns
        # a direct reference, not a copy), so mutating it via that call
        # updates trade.m1_opposite_ob_count in place too; comparing
        # AFTER calling it would double-count new_sightings.
        already_counted = trade.m1_opposite_ob_count
        newest_start_time = max(z.start_time for z in new_sightings)
        tracker.record_m1_opposite_obs(symbol, len(new_sightings), newest_start_time)
        if already_counted + len(new_sightings) >= 2:
            tracker.close_trade(symbol, block=True)
            return True

    if trade.m3_watch_start_time is not None:
        zone = store.get(symbol, "3", trade.direction, trade.m3_watch_start_time)
        if zone is None:
            tracker.close_trade(symbol, block=True)
            return True
    return False


def _read_live_close(path: str, symbol: str, timeframe: str) -> Optional[float]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    entry = raw.get(f"{symbol}|{timeframe}")
    if entry is None:
        return None
    close = entry.get("close")
    return float(close) if close is not None else None


def _price_crossed(direction: str, entry_price: float, current_price: float) -> bool:
    """True once price has come back to (or through) a pending
    retracement entry -- bull entries sit below current price at
    proposal time (price must fall to reach them), bear entries sit
    above (price must rise)."""
    return current_price <= entry_price if direction == "bull" else current_price >= entry_price


def _apply_sl_cap(sym_cfg: SymbolConfig, direction: str, entry_price: float, sl: float) -> float:
    """Clamps SL to sym_cfg.max_sl_points from entry if it would
    otherwise be wider -- v3's own copy of
    reversal_manager._apply_sl_cap's identical logic. Added 2026-08-20
    after a real (non-stale) parent OB produced a genuine ~33-point SL
    when XAUUSD rallied hard between the parent forming and the trigger
    firing. No-op (returns sl unchanged) when max_sl_points is None
    (every symbol except XAUUSD, for now)."""
    if sym_cfg.max_sl_points is None:
        return sl
    distance = (entry_price - sl) if direction == "bull" else (sl - entry_price)
    if distance <= sym_cfg.max_sl_points:
        return sl
    return entry_price - sym_cfg.max_sl_points if direction == "bull" else entry_price + sym_cfg.max_sl_points


def _atr_confirms(atr_store: AtrStore, symbol: str, timeframe: str, direction: str, after_time: int) -> bool:
    """True if this timeframe's own ATR trend currently agrees with
    direction AND last flipped to it strictly after after_time (the
    parent OB's own formation time) -- mirrors the "post-parent-
    formation, freshest-only" requirement _newest_post_parent_zone
    already applies to OB-based triggers, just for the ATR signal
    instead. AtrStore's own event_time is already debounced against
    intrabar noise by AtrTrendTracker (2 consecutive polls to commit a
    flip) -- see that module's own docstring."""
    state = atr_store.get(symbol, timeframe)
    if state is None:
        return False
    wants_trend = 1 if direction == "bull" else -1
    return state.trend == wants_trend and state.event_time is not None and state.event_time > after_time


def _try_fire_entry_atr_or_ob(store: ZoneStore, tracker: TradeTracker, sym_cfg: SymbolConfig,
                               active: ActiveTrade) -> None:
    """USOIL/USTEC's own firing mechanism -- user's explicit rule
    2026-08-19, see SymbolConfig.atr_confirm_timeframe's own docstring
    for the full quote. Only ever called while active.status is
    AWAITING_TRIGGER (the caller already returns early on FILLED, and
    this mechanism never produces a PENDING state to revisit -- always
    an immediate market fire, never a pullback proposal)."""
    symbol = sym_cfg.symbol
    timeframe = sym_cfg.atr_confirm_timeframe
    direction = active.direction

    zone = _newest_post_parent_zone(store, tracker, symbol, timeframe, direction, active.parent_start_time)
    ob_confirms = zone is not None

    atr_store = AtrStore(sym_cfg.atr_state_file)
    atr_confirms = _atr_confirms(atr_store, symbol, timeframe, direction, active.parent_start_time)

    if not ob_confirms and not atr_confirms:
        return

    # SL buffer not configured yet (user said "pending, I'll give
    # later") -- skip cleanly rather than let entries.SYMBOL_SL_BUFFER's
    # unguarded dict lookup raise. Trend Manager's own run_once has no
    # per-symbol try/except (unlike Reversal Manager's), so an
    # unhandled KeyError here would skip the ENTIRE cycle for every
    # OTHER symbol too, not just this one -- must not let that happen.
    if symbol not in entries.SYMBOL_SL_BUFFER:
        print(f"[trend_manager] {symbol}: {_DIRECTION_LABELS[direction]} entry ready "
              f"({'OB' if ob_confirms else 'ATR'} confirmed) but SL buffer not configured yet -- skipping")
        return

    current_price = _read_live_close(sym_cfg.live_state_file, symbol, timeframe)
    if current_price is None:
        return

    parent_zone = store.get(symbol, active.parent_timeframe, active.direction, active.parent_start_time)
    if parent_zone is None:
        print(f"[trend_manager] {symbol}: parent OB ({active.parent_timeframe}, "
              f"{active.parent_start_time}) missing from the store -- skipping this cycle's entry")
        return
    sl = entries.initial_sl_from_parent(symbol, direction, parent_zone.top, parent_zone.btm)
    sl = _apply_sl_cap(sym_cfg, direction, current_price, sl)

    # exec_start_time needs a stable identity either way -- the OB's own
    # start_time when that's what confirmed, else the ATR flip's own
    # event_time (both are real, distinct epoch timestamps).
    start_time = zone.start_time if ob_confirms else int(atr_store.get(symbol, timeframe).event_time)
    reason = "fresh OB" if ob_confirms else "ATR flip"

    tracker.fill_market(symbol, timeframe, start_time, current_price, sl, via_atr=not ob_confirms)
    tf_label = _TF_LABELS.get(timeframe, timeframe)
    direction_label = _DIRECTION_LABELS[direction]
    print(f"[trend_manager] {symbol}: TRADE SIGNAL {direction_label} FILLED (market) via {tf_label} "
          f"({reason} confirmation) @ {current_price:.2f} SL={sl} (not yet wired to MT5 -- signal only)")


def _try_fire_entry(store: ZoneStore, tracker: TradeTracker, sym_cfg: SymbolConfig, active: ActiveTrade) -> None:
    """Scans trigger timeframes for the best current entry-plan
    candidate and, if it's new/better than whatever's already proposed,
    fires it (MARKET fills immediately, PENDING gets proposed/replaced).
    Mutates tracker; only ever prints."""
    symbol = sym_cfg.symbol
    best_plan = None  # (mode, entry_price, timeframe, start_time, distance, current_price, top, btm)
    for timeframe in sym_cfg.trigger_timeframes:
        zone = _newest_post_parent_zone(store, tracker, symbol, timeframe, active.direction, active.parent_start_time)
        if zone is None:
            continue
        current_price = _read_live_close(sym_cfg.live_state_file, symbol, timeframe)
        if current_price is None:
            continue
        edge = entries.ob_edge(active.direction, zone.top, zone.btm)
        plan = entries.compute_entry(symbol, timeframe, active.direction, edge, current_price)
        if plan.mode == entries.EntryMode.NONE:
            continue
        distance = 0.0 if plan.mode == entries.EntryMode.MARKET else abs(plan.entry_price - current_price)
        candidate = (plan.mode, plan.entry_price, timeframe, zone.start_time, distance, current_price, zone.top, zone.btm)
        if best_plan is None or distance < best_plan[4]:
            best_plan = candidate

    if best_plan is None:
        return

    mode, entry_price, timeframe, start_time, _distance, current_price, top, btm = best_plan
    already_proposed = (active.status != "AWAITING_TRIGGER"
                         and active.exec_timeframe == timeframe and active.exec_start_time == start_time)
    if already_proposed:
        return

    # SL is based on the PARENT OB's own edge, not whichever trigger
    # timeframe (M1/M3/M5) actually fired the entry -- user's explicit
    # correction 2026-08-19 (see entries.initial_sl_from_parent's own
    # docstring). The parent zone is re-fetched fresh here rather than
    # cached on ActiveTrade since its top/btm never change once formed,
    # but re-fetching costs nothing and avoids a second source of truth.
    # A parent zone this trade's own bias depends on being missing from
    # the store would mean something upstream is already badly wrong
    # (bias-flip/mitigation logic should have reacted first) -- skip
    # firing rather than fall back to a DIFFERENT SL basis than what was
    # decided, silently.
    parent_zone = store.get(symbol, active.parent_timeframe, active.direction, active.parent_start_time)
    if parent_zone is None:
        print(f"[trend_manager] {symbol}: parent OB ({active.parent_timeframe}, "
              f"{active.parent_start_time}) missing from the store -- skipping this cycle's entry")
        return
    sl = entries.initial_sl_from_parent(symbol, active.direction, parent_zone.top, parent_zone.btm)
    effective_entry = current_price if mode == entries.EntryMode.MARKET else entry_price
    sl = _apply_sl_cap(sym_cfg, active.direction, effective_entry, sl)
    tf_label = _TF_LABELS.get(timeframe, timeframe)
    direction_label = _DIRECTION_LABELS[active.direction]

    # XAUUSD only, M1 trigger only -- capture whichever M3 OB is "in
    # play" (same selection an M3-triggered entry would itself use)
    # right now, so _close_if_m1_noise_exit has a specific zone to
    # watch for mitigation later. None if no such M3 OB exists yet --
    # only the two opposite-OB conditions apply for this trade then.
    m3_watch_start_time = None
    if timeframe == "1":
        m3_zone = _newest_post_parent_zone(store, tracker, symbol, "3", active.direction, active.parent_start_time)
        m3_watch_start_time = m3_zone.start_time if m3_zone is not None else None

    if mode == entries.EntryMode.MARKET:
        tracker.fill_market(symbol, timeframe, start_time, current_price, sl, m3_watch_start_time=m3_watch_start_time)
        print(f"[trend_manager] {symbol}: TRADE SIGNAL {direction_label} FILLED (market) via {tf_label} "
              f"@ {current_price:.2f} SL={sl} (not yet wired to MT5 -- signal only)")
    else:
        was_pending = active.status == "PENDING"
        tracker.propose_pending(symbol, timeframe, start_time, entry_price, sl, m3_watch_start_time=m3_watch_start_time)
        verb = "replaced with" if was_pending else "proposed"
        print(f"[trend_manager] {symbol}: TRADE SIGNAL {direction_label} PENDING {verb} via {tf_label} "
              f"@ {entry_price:.2f} SL={sl} (not yet wired to MT5 -- signal only)")


def _check_close_event(tracker: TradeTracker, symbol: str, manual_events_file: str) -> bool:
    """Reads Execution Bridge's own event file (manual_events.py,
    read-only from here) -- if it carries a real-world close (manual
    cancel/close, or a genuine SL/TP hit) for this symbol that Trend
    Manager hasn't already reacted to AND that's still about whatever
    trade is currently active (not an older one Trend Manager has since
    moved past on its own), closes and permanently blocks the current
    trade, same treatment as a bias flip. Returns True if a close
    happened this call.

    The identity check (not just "is anything active for this symbol")
    matters -- real live bug, confirmed 2026-08-25: see
    should_react_to_close_event's own docstring for the full USTEC
    incident this fixes."""
    event = manual_events.read_event(manual_events_file, symbol)
    if event is None:
        return False
    event_time, exec_timeframe, exec_start_time = event
    if not tracker.should_react_to_close_event(symbol, event_time, exec_timeframe, exec_start_time):
        return False
    if tracker.active_trade(symbol) is None:
        return False  # nothing currently open/pending to close
    tracker.close_trade(symbol, block=True)
    print(f"[trend_manager] {symbol}: real close detected in MT5 (manual/SL/TP) -- closing trade, blocking that OB")
    return True


def _run_trade_logic(store: ZoneStore, tracker: TradeTracker, sym_cfg: SymbolConfig,
                      manual_events_file: str) -> None:
    """One cycle of the full trade-initiation + entry-execution state
    machine for one symbol -- see this module's own docstring for the
    full rule. Mutates tracker (persists itself); only ever prints,
    never touches MT5."""
    symbol = sym_cfg.symbol
    parent_timeframes = sym_cfg.parent_timeframes

    # M1-triggered trades (XAUUSD only) use a dedicated, noise-tolerant
    # exit instead of the normal same-OB-mitigation check -- see
    # _close_if_m1_noise_exit's own docstring for the full rule.
    existing = tracker.active_trade(symbol)
    if existing is not None and existing.exec_timeframe == "1":
        if _close_if_m1_noise_exit(store, tracker, symbol, existing):
            print(f"[trend_manager] {symbol}: M1 noise-tolerant exit condition met -- closing trade")
    elif tracker.close_if_invalidated(symbol, store):
        print(f"[trend_manager] {symbol}: active trade's reference OB was mitigated -- treating as closed")

    _check_close_event(tracker, symbol, manual_events_file)

    active = tracker.active_trade(symbol)

    # Opposite-direction parent OB flips the bias -- close/block current, if
    # any -- but only if it's genuinely NEWER than the active trade's own
    # parent OB, not merely eligible. Real live incident, confirmed
    # 2026-08-25: an M5 bullish OB and an M15 bearish OB formed on the exact
    # same candle-open (identical start_time) -- the M5 side won bias first
    # and fired a trade because M5 confirms faster, then ~10 minutes later
    # the M15 side finished confirming and, under the old "any eligible
    # opposite OB flips" rule, closed that trade immediately even though its
    # own OB was no more recent than the one already winning -- just slower
    # to confirm. User's correction: "m5 has the recent bullish ob, which
    # wins the bias" -- a same-age (or older) opposite OB shouldn't override
    # an already-active, already-firing bias just because it confirmed
    # later; only a genuinely newer opposite OB should.
    if active is not None:
        opposite_direction = "bear" if active.direction == "bull" else "bull"
        flip = _best_parent_candidate(store, tracker, symbol, parent_timeframes, (opposite_direction,))
        if flip is not None and flip[0] > active.parent_start_time:
            print(f"[trend_manager] {symbol}: newer opposite-direction parent OB appeared -- bias flip, "
                  f"closing {active.direction} trade")
            tracker.close_trade(symbol, block=True)
            active = None

    # No active trade (never was, or just flipped) -- decide bias.
    if active is None:
        best = _best_parent_candidate(store, tracker, symbol, parent_timeframes, ("bull", "bear"))
        if best is None:
            return
        start_time, timeframe, direction = best
        tracker.set_parent(symbol, direction, timeframe, start_time)
        active = tracker.active_trade(symbol)
        tf_label = _TF_LABELS.get(timeframe, timeframe)
        print(f"[trend_manager] {symbol}: bias {_DIRECTION_LABELS[direction]} via parent {tf_label} "
              f"OB @ {start_time}")

    # Block any NEW same-direction parent OB on EITHER parent timeframe (no pyramiding).
    # Skips the active trade's OWN parent OB (same timeframe, same start_time) --
    # fixed 2026-08-20: this loop runs every cycle including the very one
    # that just picked that OB as parent above, so without this skip it
    # immediately watermarked (blocked) the just-chosen parent OB before
    # _try_fire_entry below ever got a chance to also use it as its own
    # trigger (see _newest_post_parent_zone's own 2026-08-20 docstring
    # update) -- confirmed live: this is what silently prevented a fresh
    # XAUUSD M5 OB from ever firing its own buy.
    #
    # Also skips a same-direction OB on a DIFFERENT parent timeframe if
    # that timeframe is ALSO one of this symbol's trigger timeframes AND
    # the trade hasn't fired yet -- fixed 2026-08-21 after a real live
    # incident: M15 won bias (M5 was NOT the newest parent this time),
    # but a genuinely fresh M5 OB formed at the exact same instant (every
    # M15 candle-open is also an M5 candle-open) -- since M5 is one of
    # XAUUSD's own trigger timeframes, that OB should have gotten a shot
    # at _try_fire_entry below as a normal trigger candidate. Instead
    # this loop treated it as just another competing parent OB and
    # blocked it outright, in the SAME cycle, before _try_fire_entry ever
    # ran -- user's own words: "it should trigger the trade if its in
    # specified range, flipping the trade is also must." Once the trade
    # actually FILLS, this exemption stops applying (status != "FILLED"
    # check below) -- no-pyramiding still holds for every cycle after
    # that, unchanged.
    #
    # A genuinely NEWER same-direction OB appearing later, or on a
    # parent timeframe that ISN'T also a trigger timeframe (M15 for
    # XAUUSD, always), is still blocked exactly as before.
    for timeframe in parent_timeframes:
        start_time = _newest_eligible_start_time(store, tracker, symbol, timeframe, active.direction)
        if start_time is None:
            continue
        is_own_parent = (timeframe == active.parent_timeframe and start_time == active.parent_start_time)
        is_live_trigger_candidate = (timeframe in sym_cfg.trigger_timeframes and active.status != "FILLED"
                                      and start_time >= active.parent_start_time)
        if is_own_parent or is_live_trigger_candidate:
            continue
        tracker.mark_traded_only(symbol, timeframe, active.direction, start_time)
        tf_label = _TF_LABELS.get(timeframe, timeframe)
        print(f"[trend_manager] {symbol}: new {active.direction} OB ({tf_label}) while trade active "
              f"-- marked traded, no new entry")

    if active.status == "FILLED":
        return  # nothing more to do until closure (mitigation) or a bias flip

    if sym_cfg.atr_confirm_timeframe is not None:
        _try_fire_entry_atr_or_ob(store, tracker, sym_cfg, active)
    else:
        _try_fire_entry(store, tracker, sym_cfg, active)

    active = tracker.active_trade(symbol)  # re-fetch -- _try_fire_entry may have mutated it
    if active is not None and active.status == "PENDING":
        current_price = _read_live_close(sym_cfg.live_state_file, symbol, active.exec_timeframe)
        if current_price is not None and _price_crossed(active.direction, active.entry_price, current_price):
            tracker.fill_pending(symbol)
            tf_label = _TF_LABELS.get(active.exec_timeframe, active.exec_timeframe)
            print(f"[trend_manager] {symbol}: TRADE SIGNAL {_DIRECTION_LABELS[active.direction]} FILLED "
                  f"(pending reached) via {tf_label} @ {active.entry_price:.2f} "
                  f"(not yet wired to MT5 -- signal only)")


def run_once(cfg: Config, tracker: TradeTracker) -> list[TrendReading]:
    readings = []
    for sym_cfg in cfg.symbols:
        store = ZoneStore(sym_cfg.zone_state_file)
        reading = compute(store, sym_cfg)
        readings.append(reading)
        print(f"[trend_manager] {_format_reading(reading)}")
        _run_trade_logic(store, tracker, sym_cfg, cfg.manual_events_file)
    return readings


def main() -> None:
    cfg = load_config()
    tracker = TradeTracker(cfg.trade_state_file)
    print(f"[trend_manager] watching {[s.symbol for s in cfg.symbols]}, polling every {cfg.poll_seconds}s")
    while True:
        try:
            run_once(cfg, tracker)
        except Exception as exc:
            print(f"[trend_manager] ERROR: {exc}")
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
