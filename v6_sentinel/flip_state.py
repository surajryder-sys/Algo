"""Flip/trap state machine on close vs. BOTH of a timeframe's own ATR
trail lines (rates.TrailSeries) -- the core signal primitive shared by
M5 Bias (M5/STR mode) and M3 execution. Same mechanism, different
timeframe's own series. Ported verbatim from v5_sentinel/flip_state.py
(2026-09-18) -- fully stateless, pure functions over a passed-in
TrailSeries, so nothing needed changing for V6S's multi-instrument
design.

Three effective states, walked bar-by-bar over closed candles only:

  CONFIRMED (bull or bear) -- price has closed beyond BOTH lines on the
  confirmed side. This is the resting state.

  WATCHING -- price closed beyond only the NEAR line (the one closer to
  price given the current confirmed side) while the FAR line hasn't been
  breached yet. Ambiguous: could resolve into a genuine flip or a trap.

  From WATCHING, the next relevant close resolves it one of two ways:
    - closes beyond the FAR line too -> FLIP: genuine reversal, confirmed
      side changes.
    - closes back beyond the NEAR line on the original side ->
      TRAP_RESOLVED: price faked the breach and snapped back: confirmed
      side reverts to what it already was, but this still counts as a
      fresh signal event -- it is NOT a silent no-op.

"Near"/"far" are relative to the CURRENTLY confirmed side, not fixed to
line1 or line2 -- whichever of the two trail values sits closer to price
is "near", the other is "far". For a bullish confirmed state (price above
both lines), near = max(trail1, trail2) (the higher, closer-to-price
line), far = min(trail1, trail2). Mirrored for bearish.

A single big candle can skip straight from CONFIRMED one side to
CONFIRMED the other side without ever passing through WATCHING (closes
beyond both lines in one bar) -- treated as a FLIP too, same as normal.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from v6_sentinel.rates import TrailSeries


class Confirmed(Enum):
    BULL = 1
    BEAR = -1


class EventType(Enum):
    FLIP = "FLIP"                   # genuine reversal
    TRAP_RESOLVED = "TRAP_RESOLVED"  # watched, then snapped back to the original side


@dataclass(frozen=True)
class FlipEvent:
    bar_index: int
    bar_time: int
    event_type: EventType
    confirmed: Confirmed   # confirmed state AFTER this event


@dataclass(frozen=True)
class FlipStateResult:
    symbol: str
    timeframe_minutes: int
    confirmed: Confirmed
    confirmed_since_time: int
    watching: Optional[Confirmed]        # direction being watched toward, None if not currently watching
    watching_since_time: Optional[int]
    last_event: Optional[FlipEvent]      # most recent FLIP/TRAP_RESOLVED within the fetched window, None if it never changed
    far_line: float                       # far line for the CURRENT confirmed direction, at the last closed bar
    near_line: float
    last_close: float
    last_time: int

    def event_just_happened(self) -> bool:
        """True if last_event fired on the very last closed bar -- i.e.
        something changed THIS bar, not further back in history."""
        return self.last_event is not None and self.last_event.bar_time == self.last_time

    def label(self) -> str:
        """Human-readable state label using the same STRONG/WEAK
        vocabulary M5 Bias already uses -- purely presentational, no
        effect on any decision logic. A settled confirmed state is
        STRONG (bull) / WEAK (bear); a watching/trap-or-flip state is
        PARTIAL STRONG or PARTIAL WEAK depending which way it's leaning,
        both explicitly flagged UNDECISIVE since neither line has fully
        cleared yet."""
        if self.watching is None:
            return "STRONG" if self.confirmed == Confirmed.BULL else "WEAK"
        return ("PARTIAL STRONG (UNDECISIVE)" if self.watching == Confirmed.BULL
                else "PARTIAL WEAK (UNDECISIVE)")


def far_near_line(direction: int, trail1: float, trail2: float) -> tuple[float, float]:
    """(far, near) for a position/confirmed-state in the given direction
    (1 bullish, -1 bearish). far = the more protective/distant line,
    near = the one closer to price. Used both internally and directly by
    sl_manager.py for a specific open trade's own direction."""
    lo, hi = min(trail1, trail2), max(trail1, trail2)
    return (lo, hi) if direction == 1 else (hi, lo)


def compute(series: TrailSeries) -> Optional[FlipStateResult]:
    """Walks the full closed-bar series once and returns the resulting
    state as of the last bar. Returns None if there isn't at least one
    bar where both trail lines have a value."""
    closes, times = series.closes, series.times
    trail1, trail2 = series.trail1, series.trail2

    start = None
    for i in range(len(closes)):
        if trail1[i] is not None and trail2[i] is not None:
            start = i
            break
    if start is None:
        return None

    lo0, hi0 = min(trail1[start], trail2[start]), max(trail1[start], trail2[start])
    if closes[start] > hi0:
        confirmed = Confirmed.BULL
    elif closes[start] < lo0:
        confirmed = Confirmed.BEAR
    else:
        confirmed = Confirmed.BULL  # bootstrap default, same convention _compute_trail_series uses
    confirmed_since_time = times[start]
    watching: Optional[Confirmed] = None
    watching_since_time: Optional[int] = None
    last_event: Optional[FlipEvent] = None

    for i in range(start + 1, len(closes)):
        if trail1[i] is None or trail2[i] is None:
            continue
        lo, hi = min(trail1[i], trail2[i]), max(trail1[i], trail2[i])
        close = closes[i]

        if watching is None:
            if confirmed == Confirmed.BULL:
                if close > hi:
                    continue  # still confirmed bull, nothing changed
                elif close < lo:
                    confirmed = Confirmed.BEAR
                    confirmed_since_time = times[i]
                    last_event = FlipEvent(i, times[i], EventType.FLIP, confirmed)
                else:
                    watching = Confirmed.BEAR
                    watching_since_time = times[i]
            else:  # confirmed BEAR
                if close < lo:
                    continue
                elif close > hi:
                    confirmed = Confirmed.BULL
                    confirmed_since_time = times[i]
                    last_event = FlipEvent(i, times[i], EventType.FLIP, confirmed)
                else:
                    watching = Confirmed.BULL
                    watching_since_time = times[i]
        else:
            if watching == Confirmed.BULL:  # came from BEAR, watching for a bullish flip
                if close > hi:
                    confirmed = Confirmed.BULL
                    confirmed_since_time = times[i]
                    last_event = FlipEvent(i, times[i], EventType.FLIP, confirmed)
                    watching = None
                    watching_since_time = None
                elif close < lo:
                    # confirmed was already BEAR -- reverting to it is a trap, not a no-op
                    confirmed_since_time = times[i]
                    last_event = FlipEvent(i, times[i], EventType.TRAP_RESOLVED, Confirmed.BEAR)
                    watching = None
                    watching_since_time = None
                # else: still ambiguous, stay watching
            else:  # watching == BEAR, came from BULL
                if close < lo:
                    confirmed = Confirmed.BEAR
                    confirmed_since_time = times[i]
                    last_event = FlipEvent(i, times[i], EventType.FLIP, confirmed)
                    watching = None
                    watching_since_time = None
                elif close > hi:
                    confirmed_since_time = times[i]
                    last_event = FlipEvent(i, times[i], EventType.TRAP_RESOLVED, Confirmed.BULL)
                    watching = None
                    watching_since_time = None

    far, near = far_near_line(confirmed.value, trail1[-1], trail2[-1])

    return FlipStateResult(
        symbol=series.symbol,
        timeframe_minutes=series.timeframe_minutes,
        confirmed=confirmed,
        confirmed_since_time=confirmed_since_time,
        watching=watching,
        watching_since_time=watching_since_time,
        last_event=last_event,
        far_line=far,
        near_line=near,
        last_close=closes[-1],
        last_time=times[-1],
    )


def with_direction_flip_after(state: Optional[FlipStateResult], direction: int, tf_minutes: int,
                              position_open_time: int) -> bool:
    """True if this timeframe's own confirmed state genuinely FLIPPED to
    match `direction` (Confirmed.BULL for a BUY, Confirmed.BEAR for a
    SELL) on a bar that closed AFTER position_open_time. Added 2026-09-22
    for sl_manager.py's own pre-breakeven improvement check (user: "when
    there's a flip in m3, sl manager can actually check and trail if
    there's a new qualifying sl" -- "flip means a bearish flip for sell
    trade and a bullish flip for a buy trade"). Mirrors
    trend_entry.find_flip_exit's own "only after entry, event not a state
    comparison" contract exactly -- just the OPPOSITE match condition
    (WITH the trade's own direction here, AGAINST it for an exit): a
    TRAP_RESOLVED (snap-back to the side it was already on) is not a
    flip and doesn't count, same as there."""
    if state is None or state.last_event is None:
        return False
    event = state.last_event
    if event.event_type != EventType.FLIP:
        return False
    if event.confirmed.value != direction:
        return False
    return event.bar_time + tf_minutes * 60 > position_open_time
