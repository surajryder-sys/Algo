"""Shared HTF-level touch-arming utility. Formerly RM-STR's own full
entry engine (HTF-line-touch + CISD-confirmation, an ATR-dual/Supertrend
counterpart to reversal_ict.py's own OB-zone design) -- RM-STR was
removed entirely 2026-09-25 (user: "lets remove RM-STR completely, we
dont want reversal manager based based on ATR"), taking find_signals(),
_check_level(), ReversalSignal, and the CISD pool constants with it (all
genuinely dead once reversal_main.py stopped calling them).

scan_touches() SURVIVES: it's generic over any htf_states dict +
LevelEligibilityStore, nothing RM-STR-specific about its own
implementation, and exit_manager_ltf.py (LTF Exit, applying to every
manager) imports it directly for its own touch-arming. See git history
around 2026-09-25 for the removed entry-engine code if it's ever needed
again.

TOUCH is LIVE PRICE (bid/ask), checked every poll cycle against every
currently untraded HTF level across however many timeframes/sources the
caller's own htf_states dict covers (both the ATR dual-trail's own two
lines AND a native Supertrend line per timeframe --
htf_levels.compute_htf_state()). The moment live price reaches a level's
value, that level becomes ARMED (persisted in
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
"""
from __future__ import annotations

from typing import Optional

from v7_sentinel.htf_levels import HTFState, LevelEligibilityStore


def scan_touches(htf_states: dict[int, Optional[HTFState]], store: LevelEligibilityStore,
                 bid: float, ask: float, bid_low: Optional[float] = None, ask_high: Optional[float] = None) -> None:
    """Live-price touch arming. This is also where each (timeframe,
    source) pair's store.sync() happens (character-change detection), so
    a caller's own later confirmation check always sees this cycle's
    freshest eligibility state."""
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
