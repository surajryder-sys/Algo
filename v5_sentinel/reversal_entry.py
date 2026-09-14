"""STR Reversal Manager -- entry engine. Design confirmed with the user
2026-09-06/07 (see htf_levels.py's own docstring for the levels/
classification side this builds on).

TOUCH is LIVE PRICE (bid/ask), checked every poll cycle against every
currently untraded HTF level -- see scan_touches(). The moment live price
reaches a level's value, that level becomes ARMED (persisted in
htf_levels.LevelEligibilityStore, survives across cycles) until it's
either traded or its parent HTF's own character changes. UNCHANGED by
the 2026-09-15 redesign below.

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

CONFIRMATION + ENTRY -- REDESIGNED 2026-09-15 around an M15 PRIMARY
STRUCTURE bias, replacing the old "3F or ST1F, either sufficient" model
entirely (3F retired outright -- user's own words: "removing 3F dual
atr flip, as it might create a lot of noise"):

  PRIMARY STRUCTURE (M15) -- structure.compute_structure_signal(tracker,
  symbol, 15), the EXACT SAME recency-arbitrated engine TM-STR's own M5
  parent bias already runs on (whichever of M15's own ATR-dual flip or
  Supertrend flip produced the MOST RECENT event wins, always decisive).
  User's own vocabulary for the two inputs, matching this project's own
  established STRONG/WEAK terms (flip_state.label()): ATR-dual bullish
  flip = "Strong", ATR-dual bearish flip = "Weak"; Supertrend bullish
  flip = "Bullish", Supertrend bearish flip = "Bearish". "this is the
  bias from Primary structure... here also recency matters" -- same
  arbitration philosophy as everywhere else in this project.

  For an armed level wanting direction D (its own role):
    - If Primary Structure's CURRENT direction == D ("M15 agrees") ->
      the trigger is EITHER M1's own fresh Supertrend flip to D
      ("ST1F") or M3's own fresh ATR-dual FLIP to D ("M3F") -- either
      alone sufficient (corrected 2026-09-15, same turn: "if primary
      structure agrees then it can enter on ST1F, or M3 Dual atr flip"
      -- M3's ATR-dual flip survives from the old unconditional "3F",
      just now scoped to only fire when M15 agrees, which is also what
      addresses the original noise concern that prompted retiring it
      unconditionally).
    - If Primary Structure's CURRENT direction != D, OR is unavailable
      (bridge stale -- no fallback, no guess) -> neither ST1F nor M3F
      fire at all; instead wait for EITHER M3's own fresh Supertrend
      flip to D ("ST3F") or M5's own fresh ATR-dual FLIP to D ("M5F")
      -- whichever happens first.
  All four are independent, privileged, bar-close-gated triggers -- two
  tiers of two, selected by whether M15 currently agrees, never all
  four competing against each other in the same cycle.

  Nothing here gates on which side of the level price currently sits, or
  requires re-testing it after the initial touch-arm -- same as before.

BAR-CLOSE-GATED, all four. "ST1F"/"ST3F" use st_bridge.fresh_flip() --
only returns a result the exact bar the MQL5 indicator's own Supertrend
trend changed (event_time == bar_time). "M3F"/"M5F" use
bridge_bar_flip.BridgeBarFlipTracker (the SAME bar-close-gated state
machine Trend Manager's own M3/M5 run on) -- only fires on a genuine
FLIP confirmed by an actual closed candle, TRAP_RESOLVED never counts.

History of what used to be here, for context: M5/M3 candle-color
triggers ("5C"/"3C") were removed 2026-09-07 after a real live incident
(H4 TRAP churn, ~150 trades, net -$62); the original gated "1F" was
removed the same day; bare "3F" (M3's own ATR dual-trail flip,
unconditional) carried the load from 2026-09-07 through 2026-09-14,
then was retired 2026-09-15 in favor of this M15-gated system -- and
the same day, reinstated as "M3F", scoped to only fire when M15 agrees
(see above).

If a trigger's own bridge data (including Primary Structure's own) is
missing/stale, that trigger simply produces no signal this cycle -- no
fallback, no guess. Staleness alerts (reversal_main.py wires
bridge_flip.StaleAlertTracker in) fire separately for M3, once per
sustained staleness episode.

SL: "ST1F" -> M1's own Supertrend line value +/- buffer, read directly
off the fresh-flip bar. "ST3F" -> M3's own Supertrend line value +/-
buffer, same mechanism, one timeframe up. "M3F"/"M5F" -> that
timeframe's own far ATR trail line, FROZEN at the exact bar that
produced the flip (BridgeBarFlipTracker.event_far_near()) +/- buffer.
Once a position is open and past breakeven, ongoing SL trailing still
follows M3's CURRENT live far line (bridge_flip.m3_far_line(),
unchanged regardless of which trigger opened the position) -- only the
INITIAL entry SL differs by trigger.

find_signals() returns EVERY level that qualifies THIS cycle, in a fixed
scan order -- all of "ST1F"'s own matches first, then "M3F"'s, then
"ST3F"'s, then
"M5F"'s (each in HTF_TIMEFRAMES_MINUTES order, support level before
resistance within a timeframe), so behaviour is deterministic rather
than scan-order-random when more than one qualifies at once.
reversal_main.py decides which one becomes the actual trade and which
get marked traded + alerted as redundant -- this module only detects
and reports, it never touches the broker or LevelEligibilityStore's
traded flag itself (mark_traded is the caller's responsibility, once it
actually acts on a signal).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v5_sentinel import st_bridge, structure
from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.flip_state import EventType
from v5_sentinel.htf_levels import HTFState, LevelEligibilityStore

_M1_MINUTES = 1
_M3_MINUTES = 3
_M5_MINUTES = 5
_M15_MINUTES = 15


@dataclass(frozen=True)
class ReversalSignal:
    direction: int              # 1 buy, -1 sell
    timeframe_minutes: int       # the HTF whose level this is
    line_no: int
    trigger: str                 # "ST1F" | "ST3F" | "M5F"
    level_value: float
    sl: float


def scan_touches(htf_states: dict[int, Optional[HTFState]], store: LevelEligibilityStore,
                 bid: float, ask: float) -> None:
    """Live-price touch arming. Call every cycle, before find_signals() --
    this is also where each timeframe's store.sync() happens (character-
    change detection), so confirmation checks always see this cycle's
    freshest eligibility state. UNCHANGED by the 2026-09-15 redesign."""
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
    one of the three M15-Primary-Structure-gated triggers this cycle --
    see module docstring for the full ST1F/ST3F/M5F design."""
    signals: list[ReversalSignal] = []

    primary = structure.compute_structure_signal(tracker, symbol, _M15_MINUTES)

    if primary is not None:
        m1 = st_bridge.fresh_flip(symbol, _M1_MINUTES)
        if m1 is not None and primary.direction == m1.trend:
            sl = m1.supertrend - sl_buffer if m1.trend == 1 else m1.supertrend + sl_buffer
            signals.extend(_scan_matching_levels(htf_states, store, m1.trend, "ST1F", sl))

        fs_m3_atr = tracker.update(symbol, _M3_MINUTES)
        if (fs_m3_atr is not None and fs_m3_atr.event_just_happened() and fs_m3_atr.last_event is not None
                and fs_m3_atr.last_event.event_type == EventType.FLIP):
            m3_atr_flip_dir = fs_m3_atr.last_event.confirmed.value
            if primary.direction == m3_atr_flip_dir:
                frozen_m3 = tracker.event_far_near(_M3_MINUTES)
                if frozen_m3 is not None:  # shouldn't be None once a FLIP has fired, but no basis to guess from
                    far_m3, _near_m3 = frozen_m3
                    sl = far_m3 - sl_buffer if m3_atr_flip_dir == 1 else far_m3 + sl_buffer
                    signals.extend(_scan_matching_levels(htf_states, store, m3_atr_flip_dir, "M3F", sl))

        m3 = st_bridge.fresh_flip(symbol, _M3_MINUTES)
        if m3 is not None and primary.direction != m3.trend:
            sl = m3.supertrend - sl_buffer if m3.trend == 1 else m3.supertrend + sl_buffer
            signals.extend(_scan_matching_levels(htf_states, store, m3.trend, "ST3F", sl))

        fs_m5 = tracker.update(symbol, _M5_MINUTES)
        if (fs_m5 is not None and fs_m5.event_just_happened() and fs_m5.last_event is not None
                and fs_m5.last_event.event_type == EventType.FLIP):
            m5_flip_dir = fs_m5.last_event.confirmed.value
            if primary.direction != m5_flip_dir:
                frozen = tracker.event_far_near(_M5_MINUTES)
                if frozen is not None:  # shouldn't be None once a FLIP has fired, but no basis to guess from
                    far, _near = frozen
                    sl = far - sl_buffer if m5_flip_dir == 1 else far + sl_buffer
                    signals.extend(_scan_matching_levels(htf_states, store, m5_flip_dir, "M5F", sl))

    return signals
