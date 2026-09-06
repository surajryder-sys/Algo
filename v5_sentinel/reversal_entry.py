"""STR Reversal Manager -- entry engine. Design confirmed with the user
2026-09-06/07 (see htf_levels.py's own docstring for the levels/
classification side this builds on).

TOUCH is LIVE PRICE (bid/ask), checked every poll cycle against every
currently untraded HTF level -- see scan_touches(). The moment live price
reaches a level's value, that level becomes ARMED (persisted in
htf_levels.LevelEligibilityStore, survives across cycles) until it's
either traded or its parent HTF's own character changes.

CONFIRMATION + ENTRY is closed-bar only, same convention as the rest of
this system -- see find_signals(). Four trigger types, all evaluated
against the LATEST CLOSED bar of their own timeframe:

  Path 1 (GATED -- price must be on the correct side of the armed level,
  using that SAME closed bar's own close, not live price):
    - M5 bullish/bearish candle (close vs open)          -> code "5C"
    - M3 bullish/bearish candle (close vs open)          -> code "3C"
    - M1 flip (flip_state.event_just_happened())          -> code "1F"

  Path 2 (PRIVILEGED -- M3 ATR flip only, fires even with price on the
  WRONG side of the level, as long as that HTF's own character hasn't
  yet changed -- i.e. it hasn't itself closed to confirm a genuine
  break):
    - M3 ATR flip (flip_state.event_just_happened())      -> code "3F"

SL:
  - "5C"/"3C"/"1F" triggers -> that SAME timeframe's own last-N-closed-bar
    swing low/high (rates.recent_swing_low/high) +/- buffer.
  - "3F" trigger -> M3's own far trail line +/- buffer (Trend Manager's
    own SL basis), NOT swing low -- confirmed 2026-09-07.

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

from v5_sentinel import flip_state, rates
from v5_sentinel.htf_levels import HTFState, LevelEligibilityStore


@dataclass(frozen=True)
class ReversalSignal:
    direction: int              # 1 buy, -1 sell
    timeframe_minutes: int       # the HTF whose level this is
    line_no: int
    trigger: str                 # "5C" / "3C" / "3F" / "1F"
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


def _bullish(series: rates.TrailSeries) -> bool:
    return series.closes[-1] > series.opens[-1]


def _bearish(series: rates.TrailSeries) -> bool:
    return series.closes[-1] < series.opens[-1]


def _flip_direction(series: Optional[rates.TrailSeries]) -> Optional[int]:
    """1/-1 if that timeframe's OWN flip_state just produced a fresh
    FLIP/TRAP_RESOLVED on its own last closed bar, else None."""
    if series is None:
        return None
    fs = flip_state.compute(series)
    if fs is None or not fs.event_just_happened():
        return None
    return fs.last_event.confirmed.value


def _swing_sl(series: rates.TrailSeries, direction: int, buffer: float, lookback: int) -> Optional[float]:
    if direction == 1:
        low = rates.recent_swing_low(series, lookback)
        return None if low is None else low - buffer
    high = rates.recent_swing_high(series, lookback)
    return None if high is None else high + buffer


def _m3_far_line_sl(m3_series: rates.TrailSeries, direction: int, buffer: float) -> float:
    far, _near = flip_state.far_near_line(direction, m3_series.trail1[-1], m3_series.trail2[-1])
    return far - buffer if direction == 1 else far + buffer


def find_signals(
    htf_states: dict[int, Optional[HTFState]],
    store: LevelEligibilityStore,
    m5_series: Optional[rates.TrailSeries],
    m3_series: rates.TrailSeries,
    m1_series: Optional[rates.TrailSeries],
    sl_buffer: float,
    swing_lookback: int,
) -> list[ReversalSignal]:
    """Every armed (touched), untraded HTF level that satisfies one of the
    4 confirmation triggers as of THIS cycle's latest closed bars. M3
    flip (Path 2) is checked first per direction since it's the
    privileged trigger; M5 candle, M3 candle, M1 flip (Path 1, gated on
    price being on the correct side of that specific level) follow."""
    m5_bull, m5_bear = (m5_series is not None and _bullish(m5_series)), (m5_series is not None and _bearish(m5_series))
    m3_bull, m3_bear = _bullish(m3_series), _bearish(m3_series)
    m3_flip_dir = _flip_direction(m3_series)
    m1_flip_dir = _flip_direction(m1_series)

    signals: list[ReversalSignal] = []

    for tf, state in htf_states.items():
        if state is None:
            continue
        for level in state.levels:
            direction = 1 if level.role == "SUPPORT" else -1
            if store.is_traded(tf, direction) or not store.is_touched(tf, level.line_no):
                continue

            trigger_code: Optional[str] = None
            triggering_series: Optional[rates.TrailSeries] = None

            if direction == 1:
                if m3_flip_dir == 1:
                    trigger_code, triggering_series = "3F", m3_series
                elif m5_bull and m5_series.closes[-1] > level.value:
                    trigger_code, triggering_series = "5C", m5_series
                elif m3_bull and m3_series.closes[-1] > level.value:
                    trigger_code, triggering_series = "3C", m3_series
                elif m1_flip_dir == 1 and m1_series is not None and m1_series.closes[-1] > level.value:
                    trigger_code, triggering_series = "1F", m1_series
            else:
                if m3_flip_dir == -1:
                    trigger_code, triggering_series = "3F", m3_series
                elif m5_bear and m5_series.closes[-1] < level.value:
                    trigger_code, triggering_series = "5C", m5_series
                elif m3_bear and m3_series.closes[-1] < level.value:
                    trigger_code, triggering_series = "3C", m3_series
                elif m1_flip_dir == -1 and m1_series is not None and m1_series.closes[-1] < level.value:
                    trigger_code, triggering_series = "1F", m1_series

            if trigger_code is None:
                continue

            if trigger_code == "3F":
                sl = _m3_far_line_sl(m3_series, direction, sl_buffer)
            else:
                sl = _swing_sl(triggering_series, direction, sl_buffer, swing_lookback)
                if sl is None:
                    continue

            signals.append(ReversalSignal(direction=direction, timeframe_minutes=tf, line_no=level.line_no,
                                          trigger=trigger_code, level_value=level.value, sl=sl))

    return signals
