"""Structure signal -- the merged ATR dual-trail-line + CISD reading for
ONE timeframe, generic enough to serve both M5 (as a parent bias) and M3
(as its own execution-side signal), and used as-is for M15 Primary
Structure (RM-STR/RM-ICT's own gate). The `tracker` argument must be the
caller's own per-symbol BridgeBarFlipTracker instance -- see
bridge_bar_flip.py's own multi-instrument note.

SUPERTREND TEMPORARILY EXCLUDED FROM ARBITRATION (confirmed with the
user 2026-09-18): "as of now we will use ATR Dual Trail and CISD, and we
see supertrend later." Supertrend's own bridge is still read every call
and kept on StructureSignal.supertrend for diagnostics/decision-log
visibility, but it can never win arbitration or gate this function's own
None-return right now -- see compute_structure_signal()'s own docstring.
Re-adding it to the active candidate list later is a small, isolated
change (this module carried all three at one point; see git history) --
_SOURCE_PRIORITY below still reserves SUPERTREND's slot for exactly that.

TWO independent structure signals actively feed into arbitration right
now, each with its own event stream:

  ATR dual trail lines (existing bridge_bar_flip.py machinery) -- three
  states:
    both lines flip below price -> "strong".
    both lines flip above price -> "weak".
    only one line crosses        -> "trap" -- ambiguous, no clear
                                     direction from this side.

  CISD (cisd_bridge.py) -- bullish CISD confirmation -> bullish
    structure; bearish CISD confirmation -> bearish structure. Reads the
    bridge's own STANDING last-known confirmation (cisd_bridge.read_cisd(),
    not fresh_cisd()) -- a "current view of this source" contract, not
    the momentary "did it confirm on the EXACT last bar" contract
    fresh_cisd()/fresh_flip() serve callers that need a one-shot trigger
    instead (reversal_entry.py/reversal_ict.py's own M3CD/M5CD).

ARBITRATION -- the core of this module: the merged structure signal is
whichever of the active sources produced its MOST RECENT clear event,
full stop. Not "trapped blocks everything" -- ATR_STRUCTURE and CISD are
independent event streams, and time decides which one currently speaks
for this timeframe. Ties (identical event_time) favor ATR_STRUCTURE over
CISD, matching this module's original tie-break order with SUPERTREND's
own slot (unused right now) still ranked first for whenever it returns.

A trap is a NON-EVENT: no timestamp, no effect on the currently-standing
signal. The signal just keeps pointing wherever the last CLEAR event
(from either active stream) left it. A trap only matters once it
RESOLVES -- and resolving is itself a fresh clear event with a fresh
timestamp, even if it resolves back to the SAME direction it was
already in. This is exactly bridge_bar_flip.py's own FLIP/TRAP_RESOLVED
semantics already -- entering "watching" never creates an event, only a
genuine FLIP or TRAP_RESOLVED does -- so the ATR side of this module
needs NO new event-detection code at all, it just reads
BridgeBarFlipTracker's own FlipStateResult.

CISD is excluded from arbitration entirely (never becomes the deciding
source, even if it's the most recent event) when its own SL basis
(cisd_bridge.sl_basis() -- nearest active swing low/high, frozen at
confirmation) is unavailable (no active swing line existed at that
moment) -- StructureSignal.sl_value must always be a real, usable price
for a future M5 Parent caller, so a CISD confirmation with no swing to
anchor an SL to simply isn't a candidate this cycle, same "no fallback,
no guess" philosophy as everywhere else in this project. Falls through
to ATR_STRUCTURE alone in that case, unaffected.

Used for M5 (parent bias) and M15 (Primary Structure) exactly as-is.
Used for M3 (execution) the identical way -- M3 gets the SAME
merged-signal treatment, not separately-checked conditions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v6_sentinel import cisd_bridge, rates, st_bridge
from v6_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v6_sentinel.flip_state import FlipStateResult, far_near_line

# Tie-break order when two sources share the exact same event_time --
# lower index wins. Matches this module's original ST-over-ATR
# preference, CISD added last.
_SOURCE_PRIORITY = {"SUPERTREND": 0, "ATR_STRUCTURE": 1, "CISD": 2}


@dataclass(frozen=True)
class StructureSignal:
    direction: int                  # 1 bullish/buy, -1 bearish/sell -- ALWAYS decisive, no "neither" state
    source: str                       # "ATR_STRUCTURE" | "CISD" right now -- "SUPERTREND" reserved for when it's reinstated, see module docstring
    event_time: int                    # bar_time of whichever event set this signal
    sl_value: float                     # the ATR far line FROZEN at the event bar (ATR_STRUCTURE, via tracker.event_far_near()), or the nearest active swing low/high FROZEN at confirmation (CISD, via cisd_bridge.sl_basis()) -- WITHOUT buffer, caller applies it. Only meaningful when this signal is being used as a PARENT (M5); M3's own copy of this field is unused.
    supertrend: Optional["st_bridge.SupertrendState"]  # diagnostics only right now, never the deciding source -- see module docstring. None if that bridge is missing/stale.
    atr: FlipStateResult              # kept for diagnostics/decision-log visibility (label(), far_line/near_line, etc.)
    cisd: Optional["cisd_bridge.CISDState"]  # the bridge's own standing CISD read, if available -- None if that bridge is missing/stale, regardless of which source ended up deciding


def compute_structure_signal(tracker: BridgeBarFlipTracker, symbol: str, tf_minutes: int) -> Optional[StructureSignal]:
    """None if ATR dual-trail (the sole REQUIRED source right now) has
    nothing to offer yet (bridge missing/stale, or not enough M-bar
    history) -- no fallback, no guess, matching this project's usual
    bridge-staleness convention. Supertrend and CISD are both OPTIONAL --
    either one's own bridge being missing/stale never blocks a result;
    Supertrend additionally never becomes the deciding source at all
    right now regardless of its own data (see module docstring).
    `tracker` must be this SYMBOL's own BridgeBarFlipTracker instance."""
    st = st_bridge.read_supertrend(symbol, tf_minutes)  # diagnostics only right now, see module docstring
    fs = tracker.update(symbol, tf_minutes)
    if fs is None:
        return None

    atr_direction = fs.confirmed.value
    # Falls back to confirmed_since_time when last_event is None (the
    # persisted state never saw a flip yet) -- always a stable timestamp
    # to compare against, never None.
    atr_event_time = fs.last_event.bar_time if fs.last_event is not None else fs.confirmed_since_time

    # fs.far_line (FlipStateResult's own field) is a LIVE re-read of the
    # bridge on every call, not frozen to the bar that actually produced
    # this ATR_STRUCTURE event -- so a parent bias sourced here could
    # drift for however long passes between the flip and the entry
    # actually firing, landing on values from a totally different bar
    # (confirmed live in V5-Sentinel: a real trade's SL matched the
    # PRE-FLIP bar's own near/support line, not the post-flip far line,
    # a ~17pt miss). tracker.event_far_near() is the tracker's own value
    # FROZEN at the exact bar that produced the event -- use that instead
    # so the parent's own initial-SL basis is anchored to the deciding
    # bar, not whatever the bridge says at whatever later moment the
    # caller happens to poll. Falls back to the live fs.far_line only
    # when no event has EVER fired yet for this timeframe (a fresh
    # tracker, nothing frozen to fall back on).
    frozen = tracker.event_far_near(tf_minutes)
    atr_sl_value = frozen[0] if frozen is not None else fs.far_line

    cisd = cisd_bridge.read_cisd(symbol, tf_minutes)

    # SUPERTREND deliberately NOT in this list right now -- st is read
    # above purely for StructureSignal.supertrend's own diagnostic value,
    # see module docstring.
    candidates = [
        (atr_event_time, "ATR_STRUCTURE", atr_direction, atr_sl_value),
    ]
    if cisd is not None:
        cisd_sl = cisd_bridge.sl_basis(cisd)
        if cisd_sl is not None:  # excluded entirely if no active swing to anchor an SL to, see module docstring
            candidates.append((cisd.last_cisd_time, "CISD", cisd_bridge.direction_of(cisd), cisd_sl))

    event_time, source, direction, sl_value = max(
        candidates, key=lambda c: (c[0], -_SOURCE_PRIORITY[c[1]])
    )
    return StructureSignal(direction=direction, source=source, event_time=event_time,
                           sl_value=sl_value, supertrend=st, atr=fs, cisd=cisd)


def event_reference(symbol: str, tf_minutes: int, event_time: int, direction: int) -> Optional[tuple[float, float]]:
    """(close, near_line) of the bar AT event_time -- the "qualifying
    price" (close) and the ATR near line to retrace toward, BOTH frozen
    at that specific historical bar via copy_rates (the bridge has no
    history endpoint at all). Works regardless of whether event_time came
    from the Supertrend side or the ATR side of compute_structure_signal()
    -- "candle close" and "nearby atr" are both about the M3 candle at
    that moment, not tied to which signal produced it. None if that exact
    bar_time isn't in the fetched window, or the ATR lines were still
    warming up then."""
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
