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

CONFIRMATION + ENTRY: ONE trigger only -- "3F", M3 ATR flip, PRIVILEGED
(fires even with price on the WRONG side of the touched level, as long
as that HTF's own character hasn't yet changed -- i.e. it hasn't itself
closed to confirm a genuine break). Bridge-only (bridge_flip.py), no
copy_rates fallback for the signal itself.

History of what used to be here, for context:
  - M5/M3 candle-color triggers ("5C"/"3C") were REMOVED entirely
    2026-09-07 after they turned out to be the mechanism behind a real
    live incident (H4 TRAP churn, ~150 trades, net -$62 on that
    timeframe alone).
  - M1 flip ("1F", the gated Path 1 trigger -- required price on the
    correct side of the level) was REMOVED entirely, same day, per the
    user's explicit direction: "remove 1f logic completely and replace
    with strict 3F confirmation." "3F" is now the sole trigger for
    every entry -- no gated path at all any more, only the privileged
    M3-flip path.

If M3's bridge data is missing/stale, no signal is produced this cycle
-- no fallback, no guess. Staleness alerts (reversal_main.py wires
bridge_flip.StaleAlertTracker in) fire separately, once per sustained
staleness episode.

SL: "3F" trigger -> M3's own far trail line +/- buffer, bridge-sourced
(bridge_flip.m3_far_line()) for consistency with the signal itself.

find_signals() returns EVERY level that qualifies THIS cycle, in a fixed
scan order (HTF_TIMEFRAMES_MINUTES order, support level before resistance
within a timeframe) so behaviour is deterministic rather than scan-order-
random when more than one qualifies at once. reversal_main.py decides
which one becomes the actual trade and which get marked traded + alerted
as redundant -- this module only detects and reports, it never touches
the broker or LevelEligibilityStore's traded flag itself (mark_traded is
the caller's responsibility, once it actually acts on a signal).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v5_sentinel.bridge_flip import BridgeFlipState, m3_far_line
from v5_sentinel.htf_levels import HTFState, LevelEligibilityStore


@dataclass(frozen=True)
class ReversalSignal:
    direction: int              # 1 buy, -1 sell
    timeframe_minutes: int       # the HTF whose level this is
    line_no: int
    trigger: str                 # always "3F" now
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


def find_signals(
    symbol: str,
    htf_states: dict[int, Optional[HTFState]],
    store: LevelEligibilityStore,
    bridge_flip: BridgeFlipState,
    bid: float,
    ask: float,
    sl_buffer: float,
) -> list[ReversalSignal]:
    """Every armed (touched), untraded HTF level whose direction matches
    M3's own fresh flip THIS cycle (bridge-only, privileged -- no gate on
    which side of the level price is currently on)."""
    m3_flip_dir = bridge_flip.check(symbol, 3, bid, ask)
    if m3_flip_dir is None:
        return []

    far = m3_far_line(symbol, m3_flip_dir)
    if far is None:
        return []  # M3 bridge stale/missing -- no SL basis, skip this cycle
    sl = far - sl_buffer if m3_flip_dir == 1 else far + sl_buffer

    signals: list[ReversalSignal] = []

    for tf, state in htf_states.items():
        if state is None:
            continue
        for level in state.levels:
            direction = 1 if level.role == "SUPPORT" else -1
            if direction != m3_flip_dir:
                continue
            if store.is_traded(tf, direction) or not store.is_touched(tf, level.line_no, level.value, level.role):
                continue

            signals.append(ReversalSignal(direction=direction, timeframe_minutes=tf, line_no=level.line_no,
                                          trigger="3F", level_value=level.value, sl=sl))

    return signals
