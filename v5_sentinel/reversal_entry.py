"""STR Reversal Manager -- entry engine. Design confirmed with the user
2026-09-06/07 (see htf_levels.py's own docstring for the levels/
classification side this builds on).

TOUCH is LIVE PRICE (bid/ask), checked every poll cycle against every
currently untraded HTF level -- see scan_touches(). The moment live price
reaches a level's value, that level becomes ARMED (persisted in
htf_levels.LevelEligibilityStore, survives across cycles) until it's
either traded or its parent HTF's own character changes.

GENUINE PULLBACK-TOUCH vs. TOUCH-BY-CHARACTER-CHANGE (confirmed 2026-09-07,
found live -- user report: "price really didnt go and touch 4384 ... the
previous candle made a flip breaking the resistance"): these are NOT the
same event. A genuine touch is live price returning to test an
ALREADY-ESTABLISHED level from the correct side (e.g. price that was
above a resistance dips back down and taps it). A line's role can also
flip (RESISTANCE<->SUPPORT) simply because price broke THROUGH it and the
next closed bar relabels it relative to the new close -- that's the level
being redefined out from under any old touch, not a fresh visit to it.
LevelEligibilityStore.mark_touched()/is_touched() now key a touch to the
(line_no, value, role) actually tested, not line_no alone, so a stale
touch from before a role-flip can never be misread as validating whatever
that same slot happens to mean now -- see htf_levels.py's mark_touched()
docstring for the full mechanism.

CONFIRMATION + ENTRY: TWO independent, privileged triggers, REVISED
2026-09-12 (user's own words: "a flip on m1 by supertrend, or a flip on
m3 which crosses both lines will make eligible to enter into the trade
... any one condition satisfying will take an entry"). Both fire even
with price on the WRONG side of the touched level, as long as that
HTF's own character hasn't yet changed -- i.e. it hasn't itself closed
to confirm a genuine break:
  - "3F" -- M3's own ATR dual-trail flip (crosses BOTH lines), bridge-
    sourced, bar-close-gated (unchanged mechanically from before).
  - "1F" -- M1's own Supertrend flip (st_bridge.fresh_flip()), bridge-
    sourced, bar-close-gated the same way on the MQL5 side already (see
    st_bridge.py's own docstring) -- NOT the same "1F" that existed
    before 2026-09-07 (that was a gated path requiring price on the
    correct side of the level; this one is privileged, same as "3F").
Neither requires the other -- whichever happens first (or both, on the
rare cycle they coincide) produces a signal.

BAR-CLOSE-GATED for both. "3F" uses bridge_bar_flip.BridgeBarFlipTracker
-- the SAME bar-close-gated state machine Trend Manager's own M3/M5
already run on -- only ever fires on a genuine FLIP confirmed by an
actual closed candle, never a live intra-bar crossing. "1F" uses
st_bridge.fresh_flip(), which only returns a result the exact bar the
MQL5 indicator's own Supertrend trend changed (event_time == bar_time)
-- same "privileged, momentary" nature, computed on the MQL5 side so
this module does no flip-detection of its own for it either. TRAP_
RESOLVED events are NEVER a trigger for "3F" (a trap resolving back to
its prior direction doesn't change the persisted direction, so it was
never detected as an edge).

History of what used to be here, for context: M5/M3 candle-color
triggers ("5C"/"3C") were REMOVED entirely 2026-09-07 after they turned
out to be the mechanism behind a real live incident (H4 TRAP churn,
~150 trades, net -$62 on that timeframe alone); the ORIGINAL "1F" (a
gated Path 1 trigger requiring price on the correct side of the level)
was removed the same day ("remove 1f logic completely and replace with
strict 3F confirmation"). The "1F" reintroduced 2026-09-12 is a
different, privileged mechanism, not a revival of that gated one.

If a trigger's own bridge data is missing/stale, that trigger simply
produces no signal this cycle -- no fallback, no guess. Staleness
alerts (reversal_main.py wires bridge_flip.StaleAlertTracker in) fire
separately for M3, once per sustained staleness episode.

SL: "3F" -> M3's own far trail line +/- buffer, FROZEN at the exact bar
that produced the flip (BridgeBarFlipTracker.event_far_near(),
2026-09-09) -- NOT a live re-read of the bridge at whatever moment the
order happens to send. "1F" -> M1's own Supertrend line value +/-
buffer, read directly off that same fresh-flip bar (the MQL5 side
already only updates it once per closed bar, so no separate freezing
mechanism is needed for this one). Once a position is open and past
breakeven, ongoing SL trailing still follows M3's CURRENT live far line
(bridge_flip.m3_far_line(), unchanged regardless of which trigger opened
the position) -- only the INITIAL entry SL differs by trigger.

find_signals() returns EVERY level that qualifies THIS cycle, in a fixed
scan order -- all of "3F"'s own matches first (HTF_TIMEFRAMES_MINUTES
order, support level before resistance within a timeframe), then all of
"1F"'s (same ordering), so behaviour is deterministic rather than
scan-order-random when more than one qualifies at once, including the
rare cycle where both triggers fire together. reversal_main.py decides
which one becomes the actual trade and which get marked traded + alerted
as redundant -- this module only detects and reports, it never touches
the broker or LevelEligibilityStore's traded flag itself (mark_traded is
the caller's responsibility, once it actually acts on a signal).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v5_sentinel import st_bridge
from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.flip_state import EventType
from v5_sentinel.htf_levels import HTFState, LevelEligibilityStore

_M1_MINUTES = 1


@dataclass(frozen=True)
class ReversalSignal:
    direction: int              # 1 buy, -1 sell
    timeframe_minutes: int       # the HTF whose level this is
    line_no: int
    trigger: str                 # "3F" or "1F"
    level_value: float
    sl: float


def scan_touches(htf_states: dict[int, Optional[HTFState]], store: LevelEligibilityStore,
                 bid: float, ask: float) -> None:
    """Live-price touch arming. Call every cycle, before find_signals() --
    this is also where each timeframe's store.sync() happens (character-
    change detection), so confirmation checks always see this cycle's
    freshest eligibility state."""
    for tf, state in htf_states.items():
        if state is None:
            continue
        store.sync(tf, state.character_event_time)
        for level in state.levels:
            direction = 1 if level.role == "SUPPORT" else -1
            if store.is_traded(tf, direction):
                continue
            touch_price = bid if level.role == "SUPPORT" else ask
            reached = (touch_price <= level.value) if level.role == "SUPPORT" else (touch_price >= level.value)
            if reached:
                store.mark_touched(tf, level.line_no, level.value, level.role)


def _scan_matching_levels(htf_states: dict[int, Optional[HTFState]], store: LevelEligibilityStore,
                          direction: int, trigger: str, sl: float) -> list[ReversalSignal]:
    """Every armed (touched), untraded HTF level whose role matches
    `direction`, tagged with whichever trigger just qualified it."""
    signals: list[ReversalSignal] = []
    for tf, state in htf_states.items():
        if state is None:
            continue
        for level in state.levels:
            lvl_dir = 1 if level.role == "SUPPORT" else -1
            if lvl_dir != direction:
                continue
            if store.is_traded(tf, direction) or not store.is_touched(tf, level.line_no, level.value, level.role):
                continue
            signals.append(ReversalSignal(direction=direction, timeframe_minutes=tf, line_no=level.line_no,
                                          trigger=trigger, level_value=level.value, sl=sl))
    return signals


def find_signals(
    symbol: str,
    htf_states: dict[int, Optional[HTFState]],
    store: LevelEligibilityStore,
    tracker: BridgeBarFlipTracker,
    sl_buffer: float,
) -> list[ReversalSignal]:
    """Every armed (touched), untraded HTF level whose direction matches
    EITHER of M3's own fresh, BAR-CLOSE-CONFIRMED ATR flip ("3F") or M1's
    own fresh Supertrend flip ("1F") -- both privileged, no gate on which
    side of the level price is currently on, either alone is sufficient
    (2026-09-12). Only a genuine FLIP counts for "3F", never a
    TRAP_RESOLVED."""
    signals: list[ReversalSignal] = []

    fs_m3 = tracker.update(symbol, 3)
    if (fs_m3 is not None and fs_m3.event_just_happened() and fs_m3.last_event is not None
            and fs_m3.last_event.event_type == EventType.FLIP):
        m3_flip_dir = fs_m3.last_event.confirmed.value
        frozen = tracker.event_far_near(3)
        if frozen is not None:  # shouldn't be None once a FLIP has fired, but no basis to guess from
            far, _near = frozen
            sl = far - sl_buffer if m3_flip_dir == 1 else far + sl_buffer
            signals.extend(_scan_matching_levels(htf_states, store, m3_flip_dir, "3F", sl))

    m1 = st_bridge.fresh_flip(symbol, _M1_MINUTES)
    if m1 is not None:
        sl = m1.supertrend - sl_buffer if m1.trend == 1 else m1.supertrend + sl_buffer
        signals.extend(_scan_matching_levels(htf_states, store, m1.trend, "1F", sl))

    return signals
