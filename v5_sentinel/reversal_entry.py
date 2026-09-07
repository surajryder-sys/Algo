"""STR Reversal Manager -- entry engine. Design confirmed with the user
2026-09-06/07 (see htf_levels.py's own docstring for the levels/
classification side this builds on).

TOUCH is LIVE PRICE (bid/ask), checked every poll cycle against every
currently untraded HTF level -- see scan_touches(). The moment live price
reaches a level's value, that level becomes ARMED (persisted in
htf_levels.LevelEligibilityStore, survives across cycles) until it's
either traded or its parent HTF's own character changes.

CONFIRMATION + ENTRY: TWO trigger types only, both flip-based, both
BRIDGE-ONLY (bridge_flip.py) -- no copy_rates fallback for the signal
itself. M5/M3 candle-color triggers ("5C"/"3C") were REMOVED entirely
2026-09-07 after they turned out to be the mechanism behind a real live
incident (H4 TRAP churn, ~150 trades, net -$62 on that timeframe alone).
The bridge-first/copy_rates-fallback design that replaced them was ITSELF
then found to have a real flaw the same day: a live incident (H4/3F SELL
reversed into H8/3F BUY 9 seconds later -- impossible for a genuine M3
flip, bars close every 3 real minutes) exposed that the bridge path and
the copy_rates path used different edge-detection logic with no shared
dedup, so switching sources mid-stream could double-fire. User's own
words: "remove dependancy of copy rates for M5,M3,M1 -- follow exactly
bridge, nothing else. if any data is stale on bridge for more than some
specified time, simple send message saying data is stale." So now:

  Path 1 (GATED -- M1 flip, price must be on the correct side of the
  level: below resistance for a sell, above support for a buy. Gate uses
  LIVE price, matching what bridge_flip.py itself evaluates against):
    - M1 flip (bridge_flip.BridgeFlipState.check(), bridge-only) -> "1F"

  Path 2 (PRIVILEGED -- M3 ATR flip only, fires even with price on the
  WRONG side of the level, as long as that HTF's own character hasn't
  yet changed -- i.e. it hasn't itself closed to confirm a genuine
  break):
    - M3 ATR flip (bridge_flip.BridgeFlipState.check(), bridge-only) -> "3F"

If a timeframe's bridge data is missing/stale, that trigger simply
produces no signal this cycle -- no fallback, no guess. Staleness alerts
(reversal_main.py wires bridge_flip.StaleAlertTracker in) fire
separately, once per sustained staleness episode.

SL:
  - "1F" trigger -> M1's own last-N-closed-bar swing low/high
    (rates.recent_swing_low/high) +/- buffer. UNCHANGED, still
    copy_rates -- the bridge publishes no OHLC at all, so there is no
    bridge alternative for real bar highs/lows.
  - "3F" trigger -> M3's own far trail line +/- buffer, ALSO switched to
    bridge-only 2026-09-07 (bridge_flip.m3_far_line()) for consistency
    with the signal itself now being bridge-sourced.

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

from v5_sentinel import rates
from v5_sentinel.bridge_flip import BridgeFlipState, m3_far_line
from v5_sentinel.htf_levels import HTFState, LevelEligibilityStore


@dataclass(frozen=True)
class ReversalSignal:
    direction: int              # 1 buy, -1 sell
    timeframe_minutes: int       # the HTF whose level this is
    line_no: int
    trigger: str                 # "3F" / "1F"
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
                store.mark_touched(tf, level.line_no)


def _swing_sl(series: rates.TrailSeries, direction: int, buffer: float, lookback: int) -> Optional[float]:
    if direction == 1:
        low = rates.recent_swing_low(series, lookback)
        return None if low is None else low - buffer
    high = rates.recent_swing_high(series, lookback)
    return None if high is None else high + buffer


def find_signals(
    symbol: str,
    htf_states: dict[int, Optional[HTFState]],
    store: LevelEligibilityStore,
    bridge_flip: BridgeFlipState,
    bid: float,
    ask: float,
    m1_series: Optional[rates.TrailSeries],
    sl_buffer: float,
    swing_lookback: int,
) -> list[ReversalSignal]:
    """Every armed (touched), untraded HTF level that satisfies one of the
    2 confirmation triggers as of THIS cycle -- both bridge-only, no
    copy_rates fallback. M3 flip (Path 2) is checked first per direction
    since it's the privileged trigger; M1 flip (Path 1, gated on live
    price) follows. m1_series is kept ONLY for the "1F" trigger's
    swing-low/high SL basis -- it plays no part in detecting the flip."""
    m3_flip_dir = bridge_flip.check(symbol, 3, bid, ask)
    m1_flip_dir = bridge_flip.check(symbol, 1, bid, ask)
    mid_price = (bid + ask) / 2

    signals: list[ReversalSignal] = []

    for tf, state in htf_states.items():
        if state is None:
            continue
        for level in state.levels:
            direction = 1 if level.role == "SUPPORT" else -1
            if store.is_traded(tf, direction) or not store.is_touched(tf, level.line_no):
                continue

            trigger_code: Optional[str] = None

            if direction == 1:
                if m3_flip_dir == 1:
                    trigger_code = "3F"
                elif m1_flip_dir == 1 and mid_price > level.value:
                    trigger_code = "1F"
            else:
                if m3_flip_dir == -1:
                    trigger_code = "3F"
                elif m1_flip_dir == -1 and mid_price < level.value:
                    trigger_code = "1F"

            if trigger_code is None:
                continue

            if trigger_code == "3F":
                far = m3_far_line(symbol, direction)
                if far is None:
                    continue  # M3 bridge stale/missing -- no SL basis, skip this cycle
                sl = far - sl_buffer if direction == 1 else far + sl_buffer
            else:
                if m1_series is None:
                    continue
                sl = _swing_sl(m1_series, direction, sl_buffer, swing_lookback)
                if sl is None:
                    continue

            signals.append(ReversalSignal(direction=direction, timeframe_minutes=tf, line_no=level.line_no,
                                          trigger=trigger_code, level_value=level.value, sl=sl))

    return signals
