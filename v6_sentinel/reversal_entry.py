"""RM-STR -- entry engine. FULL REDESIGN (confirmed with the user
2026-09-19, "big changes in RM STR") -- this is NOT the M15-Primary-
Structure-gated ST1F/M3F/ST3F/M5F system this module used to run (see
git history for that design). No more M15 Primary Structure or
`structure.py` dependency at all for RM-STR -- this component is now
purely HTF-line-touch + CISD-confirmation driven, the same overall
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

SL: the CONFIRMING CISD's own nearest active swing low/high
(cisd_bridge.sl_basis()), frozen at the exact bar CISD confirmed, +/-
buffer -- NOT the touched HTF line's own value (confirmed with the user
2026-09-19, "instead can we use swing low high"). "We enter trade based
on the trigger timeframe" (the HTF line being defended decides WHICH
level is eligible and its own direction), "and execute based on cisd
timeframe" (the CISD confirmation's own bar/swing is what actually
prices and fires the order, whichever of M3/M5 got there first). See
_check_level()'s own docstring for the exact mechanics.

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

from v6_sentinel import cisd_bridge
from v6_sentinel.htf_levels import HTFState, LevelEligibilityStore

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


def scan_touches(htf_states: dict[int, Optional[HTFState]], store: LevelEligibilityStore,
                 bid: float, ask: float) -> None:
    """Live-price touch arming. Call every cycle, before find_signals() --
    this is also where each (timeframe, source) pair's store.sync()
    happens (character-change detection), so confirmation checks always
    see this cycle's freshest eligibility state."""
    for tf, state in htf_states.items():
        if state is None:
            continue
        for level in state.levels:
            store.sync(tf, level.source, level.character_event_time)
            direction = 1 if level.role == "SUPPORT" else -1
            if store.is_traded(tf, level.source, direction):
                continue
            touch_price = bid if level.role == "SUPPORT" else ask
            reached = (touch_price <= level.value) if level.role == "SUPPORT" else (touch_price >= level.value)
            if reached:
                store.mark_touched(tf, level.source, level.line_no, level.value, level.role)


def _check_level(store: LevelEligibilityStore, symbol: str, tf: int, level, sl_buffer: float) -> Optional[ReversalSignal]:
    """A single ReversalSignal if this TOUCHED, untraded level has a
    fresh, matching-direction CISD confirmation THIS cycle from either
    M3 or M5 (checked in that order -- an arbitrary but deterministic
    tie-break for the rare case both fire the exact same cycle) -- None
    otherwise.

    SL basis (confirmed with the user 2026-09-19, "instead can we use
    swing low high"): NOT the touched HTF line's own value -- the
    CONFIRMING CISD's own tracked nearest active swing low/high
    (cisd_bridge.sl_basis()), frozen at the exact bar CISD confirmed.
    Same basis this project's CISD triggers always used elsewhere
    (reversal_ict.py's own OB-zone redesign uses the zone's own edge
    instead, a deliberate difference for that component -- RM-STR goes
    back to the swing basis here). sl_basis() returning None (no active
    swing line existed at confirmation) means this trigger simply
    produces no signal this cycle -- no fallback, no guess, same
    philosophy every other trigger in this project already follows."""
    direction = 1 if level.role == "SUPPORT" else -1
    if store.is_traded(tf, level.source, direction) or not store.is_touched(
            tf, level.source, level.line_no, level.value, level.role):
        return None
    for tf_minutes in _CISD_POOL:
        cisd = cisd_bridge.fresh_cisd(symbol, tf_minutes)
        if cisd is None or cisd_bridge.direction_of(cisd) != direction:
            continue
        basis = cisd_bridge.sl_basis(cisd)
        if basis is None:
            continue
        sl = basis - sl_buffer if direction == 1 else basis + sl_buffer
        return ReversalSignal(direction=direction, timeframe_minutes=tf, source=level.source,
                              line_no=level.line_no, trigger=_CISD_TAG[tf_minutes],
                              level_value=level.value, sl=sl)
    return None


def find_signals(symbol: str, htf_states: dict[int, Optional[HTFState]], store: LevelEligibilityStore,
                 sl_buffer: float) -> list[ReversalSignal]:
    """Every armed (touched), untraded HTF level (ATR dual-trail or
    Supertrend, any of the 8 timeframes) with a fresh matching-direction
    CISD confirmation this cycle -- see module docstring for the full
    design."""
    signals: list[ReversalSignal] = []
    for tf, state in htf_states.items():
        if state is None:
            continue
        for level in state.levels:
            sig = _check_level(store, symbol, tf, level, sl_buffer)
            if sig is not None:
                signals.append(sig)
    return signals
