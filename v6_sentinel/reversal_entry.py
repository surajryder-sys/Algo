"""RM-STR -- entry engine. FULL REDESIGN (confirmed with the user
2026-09-19, "big changes in RM STR") -- this is NOT the M15-Primary-
Structure-gated ST1F/M3F/ST3F/M5F system this module used to run (see
git history for that design). No more M15 Primary Structure
dependency at all for RM-STR -- this component is now purely HTF-line-touch + CISD-confirmation driven, the same overall
mechanism RM-ICT's own redesign uses (reversal_ict.py), just applied to
ATR-dual/Supertrend lines instead of OB zones.

TOUCH is LIVE PRICE (bid/ask), checked every poll cycle against every
currently untraded HTF level across 8 timeframes (D1, H4, H2, H1, M30,
M15, M10, M5 -- htf_levels.HTF_TIMEFRAMES_MINUTES) and BOTH sources per
timeframe (the ATR dual-trail's own two lines, PLUS a native Supertrend
line -- htf_levels.compute_htf_state()). The moment live price reaches a
level's value, that level becomes ARMED (persisted in
htf_levels.LevelEligibilityStore, survives across cycles) until it's
either traded or that SPECIFIC source's own character changes (ATR and
Supertrend are tracked independently -- a Supertrend flip never resets
ATR's own eligibility for the same timeframe, and vice versa).

GENUINE PULLBACK-TOUCH vs. TOUCH-BY-CHARACTER-CHANGE: these are NOT the
same event. A genuine touch is live price returning to test an
ALREADY-ESTABLISHED level from the correct side. A line's role can also
flip (RESISTANCE<->SUPPORT) simply because price broke THROUGH it and the
next closed bar relabels it relative to the new close -- that's the level
being redefined out from under any old touch, not a fresh visit to it.
LevelEligibilityStore.mark_touched()/is_touched() key a touch to
(source, line_no, value, role), so a stale touch from before a
value-change or role-flip can never be misread as validating whatever
that same slot happens to mean now.

CONFIRMATION: once a level is touched (armed) and untraded, wait for a
FRESH CISD confirmation in the MATCHING direction, from EITHER M3 or M5
-- whichever fires first (checked every cycle, both pools, uniformly for
all 8 HTF timeframes -- no special-casing, unlike RM-ICT's own
zone-timeframe-dependent pools, since the user's own description here
gave one single rule for the whole scope: "confirmation from M3, M5
CISD, whichever confirms fast"). Uses cisd_bridge.fresh_cisd() (the
"privileged, momentary, only non-None the EXACT bar it confirmed"
contract), not read_cisd() (standing state) -- same reasoning as
reversal_ict.py's own docstring: a CISD that's already sitting in the
matching direction before the touch doesn't count, only a genuinely
fresh confirmation after does, and fresh_cisd()'s own one-shot contract
delivers that naturally with no explicit touch-timestamp bookkeeping.

INITIAL SL (confirmed with the user 2026-09-19 -- swing high/low is the
FALLBACK, lines are preferred when one is usable):
  1. M5's own lines first (ATR dual line1/line2 + Supertrend), then M3's
     if M5 has none -- any single line is enough. A line is USABLE only
     if it sits on the correct side of the live entry price: ABOVE it
     for a SELL, BELOW it for a BUY (a line on the wrong side, e.g. every
     line below price as support during a bearish-CISD SELL, can't be a
     stop). If several are usable on that timeframe, the FARTHEST from
     entry wins (widest SL -- user's explicit choice over nearest).
     SL = that line's value +/- buffer. NOTE: there is no maximum
     distance cap on this -- a far ATR line can put the stop a long way
     from entry.
  2. Only if neither M5 nor M3 has a usable line: the confirming CISD's
     own nearest active swing high (SELL) / low (BUY),
     cisd_bridge.sl_basis(), frozen at the exact bar CISD confirmed, +/-
     buffer. If there's no active swing either, that CISD produces no
     signal this cycle (no fallback, no guess).
M3's lines aren't part of the HTF scope (htf_levels stops at M5), so
they're computed on demand (sl_basis.py), only when a signal is actually
about to fire. "We enter trade based on the trigger timeframe" (the touched HTF
line decides WHICH level is eligible and its direction), "and execute
based on cisd timeframe" (the CISD bar is what fires the order). See
sl_basis.py's own docstring for the exact mechanics (shared with RM-ICT).

find_signals() returns EVERY level that qualifies THIS cycle, in a fixed
scan order (HTF_TIMEFRAMES_MINUTES order, ATR line1/line2 then
Supertrend within a timeframe), so behaviour is deterministic rather
than scan-order-random when more than one qualifies at once. The caller
decides which one becomes the actual trade and which get marked traded
+ alerted as redundant -- this module only detects and reports, it
never touches the broker or LevelEligibilityStore's traded flag itself
(mark_traded is the caller's responsibility, once it actually acts on a
signal).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v6_sentinel import cisd_bridge, sl_basis
from v6_sentinel.htf_levels import HTFState, LevelEligibilityStore
from v6_sentinel.sideways_trapper import SidewaysTrapper

_CISD_POOL = (3, 5)  # M3, M5 -- uniform across every HTF timeframe here, whichever confirms first
_CISD_TAG = {3: "M3CD", 5: "M5CD"}
_SOURCE_SHORT = {"ATR": "ATR", "SUPERTREND": "ST"}


@dataclass(frozen=True)
class ReversalSignal:
    direction: int              # 1 buy, -1 sell
    timeframe_minutes: int       # the HTF whose level this is
    source: str                   # "ATR" or "SUPERTREND"
    line_no: int
    trigger: str                   # "M3CD" | "M5CD" -- whichever CISD timeframe fired first
    level_value: float
    sl: float
    sl_source: str                  # where the SL basis came from, for the decision log: "M5/ATR1", "M3/ST", "SWING", ...


def scan_touches(htf_states: dict[int, Optional[HTFState]], store: LevelEligibilityStore,
                 bid: float, ask: float, bid_low: Optional[float] = None, ask_high: Optional[float] = None) -> None:
    """Live-price touch arming. Call every cycle, before find_signals() --
    this is also where each (timeframe, source) pair's store.sync()
    happens (character-change detection), so confirmation checks always
    see this cycle's freshest eligibility state."""
    # bid_low / ask_high are the lowest bid / highest ask of every tick since the previous cycle, so a
    # wick shorter than the poll interval still counts as a touch (broker.price_extremes_since).
    low = bid if bid_low is None else min(bid, bid_low)
    high = ask if ask_high is None else max(ask, ask_high)
    for tf, state in htf_states.items():
        if state is None:
            continue
        for level in state.levels:
            store.sync(tf, level.source, level.character_event_time)
            direction = 1 if level.role == "SUPPORT" else -1
            if store.is_traded(tf, level.source, direction):
                continue
            touch_price = low if level.role == "SUPPORT" else high
            reached = (touch_price <= level.value) if level.role == "SUPPORT" else (touch_price >= level.value)
            if reached:
                store.mark_touched(tf, level.source, level.line_no, level.value, level.role)


def _check_level(store: LevelEligibilityStore, symbol: str, tf: int, level, sl_buffer: float,
                 bid: float, ask: float, cache: dict, trapper: Optional[SidewaysTrapper] = None,
                 trap_min_distance: Optional[float] = None,
                 m15_structure: Optional[int] = None) -> Optional[ReversalSignal]:
    """A single ReversalSignal if this TOUCHED, untraded level has a
    fresh, matching-direction CISD confirmation THIS cycle from either
    M3 or M5 (checked in that order -- an arbitrary but deterministic
    tie-break for the rare case both fire the exact same cycle) AND a
    usable initial SL exists (see sl_basis.initial_sl_basis()) -- None
    otherwise. Entry price for the "correct side" test is the live ask
    for a BUY, the live bid for a SELL -- what the market order would
    actually fill at.

    trapper (sideways_trapper.SidewaysTrapper, optional): if given, a
    direction whose entry price would land too close to that direction's
    last SL-hit price is skipped here too -- see that module's own
    docstring for the full rule."""
    direction = 1 if level.role == "SUPPORT" else -1
    if store.is_traded(tf, level.source, direction) or not store.is_touched(
            tf, level.source, level.line_no, level.value, level.role):
        return None
    entry_price = ask if direction == 1 else bid
    if (trapper is not None and trap_min_distance is not None
            and trapper.blocks(direction, entry_price, trap_min_distance, m15_structure)):
        return None
    for tf_minutes in _CISD_POOL:
        cisd = cisd_bridge.fresh_cisd(symbol, tf_minutes)
        if cisd is None or cisd_bridge.direction_of(cisd) != direction:
            continue
        resolved = sl_basis.initial_sl_basis(symbol, direction, entry_price, cisd, cache)
        if resolved is None:
            continue
        basis, sl_source = resolved
        sl = basis - sl_buffer if direction == 1 else basis + sl_buffer
        return ReversalSignal(direction=direction, timeframe_minutes=tf, source=level.source,
                              line_no=level.line_no, trigger=_CISD_TAG[tf_minutes],
                              level_value=level.value, sl=sl, sl_source=sl_source)
    return None


def find_signals(symbol: str, htf_states: dict[int, Optional[HTFState]], store: LevelEligibilityStore,
                 sl_buffer: float, bid: float, ask: float, trapper: Optional[SidewaysTrapper] = None,
                 trap_min_distance: Optional[float] = None,
                 m15_structure: Optional[int] = None) -> list[ReversalSignal]:
    """Every armed (touched), untraded HTF level (ATR dual-trail or
    Supertrend, any of the 8 timeframes) with a fresh matching-direction
    CISD confirmation this cycle and a usable initial SL -- see module
    docstring for the full design. trapper/trap_min_distance/m15_structure:
    see _check_level's own docstring (the Sideways Trapper guard)."""
    signals: list[ReversalSignal] = []
    cache: dict = {}
    for tf, state in htf_states.items():
        if state is None:
            continue
        for level in state.levels:
            sig = _check_level(store, symbol, tf, level, sl_buffer, bid, ask, cache,
                               trapper, trap_min_distance, m15_structure)
            if sig is not None:
                signals.append(sig)
    return signals
