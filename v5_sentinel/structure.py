"""Structure signal -- the merged Supertrend + ATR dual-trail-line
reading for ONE timeframe, generic enough to serve both M5 (as Trend
Manager's parent bias) and M3 (as its own execution-side signal). Design
confirmed with the user 2026-09-12, full transcript reasoning below.

TWO independent structure signals feed into this, each with its own
event stream:

  Supertrend (single line) -- always unambiguous, no trap state:
    flips to the buy side  -> "bullish", record event time.
    flips to the sell side -> "bearish", record event time.

  ATR dual trail lines (existing bridge_bar_flip.py machinery) -- three
  states:
    both lines flip below price -> "strong".
    both lines flip above price -> "weak".
    only one line crosses        -> "trap" -- ambiguous, no clear
                                     direction from this side.

ARBITRATION -- the core of this module: the merged structure signal is
whichever of the two produced its MOST RECENT clear event, full stop.
Not "trapped blocks everything" -- Supertrend and ATR-structure are two
independent event streams, and time decides which one currently speaks
for this timeframe.

A trap is a NON-EVENT: no timestamp, no effect on the currently-standing
signal. The signal just keeps pointing wherever the last CLEAR event
(from either stream) left it. A trap only matters once it RESOLVES --
and resolving is itself a fresh clear event with a fresh timestamp, even
if it resolves back to the SAME direction it was already in (matches the
user's own worked example: bullish Supertrend @ 9:15, ATR weak @ 9:45
[bias=weak], ATR traps [no event, bias still weak-since-9:45], ATR
resolves BACK to weak @ 10:10 [fresh event, bias=weak-since-10:10, same
direction but a genuinely later timestamp], ATR crosses both lines up
@ 10:30 [bias=strong/bullish]). This is exactly bridge_bar_flip.py's own
FLIP/TRAP_RESOLVED semantics already -- entering "watching" never
creates an event, only a genuine FLIP or TRAP_RESOLVED does -- so the
ATR side of this module needs NO new event-detection code at all, it
just reads BridgeBarFlipTracker's own FlipStateResult.

Used for M5 (Trend Manager's parent bias) exactly as-is. Used for M3
(execution) the identical way -- M3 gets the SAME merged-signal
treatment as M5, not two separately-checked conditions, so there's one
"M3's own qualifying price" regardless of which of its two signals
actually produced the qualifying event (see main.py's own M3 execution
docstring for how the two feed into Trigger 1 / Trigger 2 there).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v5_sentinel import rates, st_bridge
from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.flip_state import FlipStateResult, far_near_line


@dataclass(frozen=True)
class StructureSignal:
    direction: int                  # 1 bullish/buy, -1 bearish/sell -- ALWAYS decisive, no "neither" state
    source: str                       # "SUPERTREND" or "ATR_STRUCTURE" -- whichever produced this signal
    event_time: int                    # bar_time of whichever event set this signal
    sl_value: float                     # supertrend line value (SUPERTREND) or the ATR far line (ATR_STRUCTURE) -- WITHOUT buffer, caller applies it. Only meaningful when this signal is being used as a PARENT (M5); M3's own copy of this field is unused.
    supertrend: "st_bridge.SupertrendState"
    atr: FlipStateResult              # kept for diagnostics/decision-log visibility (label(), far_line/near_line, etc.)


def compute_structure_signal(tracker: BridgeBarFlipTracker, symbol: str, tf_minutes: int) -> Optional[StructureSignal]:
    """None if either source has nothing to offer yet (Supertrend bridge
    missing/stale, or not enough M-bar history for the ATR side) -- no
    fallback, no guess, matching this project's usual bridge-staleness
    convention."""
    st = st_bridge.read_supertrend(symbol, tf_minutes)
    fs = tracker.update(symbol, tf_minutes)
    if st is None or fs is None:
        return None

    atr_direction = fs.confirmed.value
    # Falls back to confirmed_since_time when last_event is None (the
    # persisted state never saw a flip yet) -- same pattern
    # htf_levels.py's own _character_event_time() uses, always a stable
    # timestamp to compare against, never None.
    atr_event_time = fs.last_event.bar_time if fs.last_event is not None else fs.confirmed_since_time

    if st.event_time >= atr_event_time:
        return StructureSignal(direction=st.trend, source="SUPERTREND", event_time=st.event_time,
                               sl_value=st.supertrend, supertrend=st, atr=fs)
    return StructureSignal(direction=atr_direction, source="ATR_STRUCTURE", event_time=atr_event_time,
                           sl_value=fs.far_line, supertrend=st, atr=fs)


def event_reference(symbol: str, tf_minutes: int, event_time: int, direction: int) -> Optional[tuple[float, float]]:
    """(close, near_line) of the bar AT event_time -- M3's own
    "qualifying price" (close) and the ATR near line to retrace toward,
    BOTH frozen at that specific historical bar via copy_rates (the
    bridge has no history endpoint at all, same reasoning
    watch_zone.py's old pullback target used). Works regardless of
    whether event_time came from the Supertrend side or the ATR side of
    compute_structure_signal() -- "candle close" and "nearby atr" are
    both about the M3 candle at that moment, not tied to which signal
    produced it. None if that exact bar_time isn't in the fetched
    window, or the ATR lines were still warming up then."""
    series = rates.read_trail_series(symbol, tf_minutes)
    if series is None:
        return None
    try:
        idx = series.times.index(event_time)
    except ValueError:
        return None
    close = series.closes[idx]
    t1, t2 = series.trail1[idx], series.trail2[idx]
    if t1 is None or t2 is None:
        return None
    _far, near = far_near_line(direction, t1, t2)
    return close, near
