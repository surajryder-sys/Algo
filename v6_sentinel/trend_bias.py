"""TM-STR's directional bias: M15 primary structure. Confirmed with the
user 2026-09-20: "m15 atr dual flip, strong/weak; m15 cisd,
bullish/bearish; recency wins."

TWO independent event streams, and whichever produced the MOST RECENT
event decides the direction:

  ATR dual-trail (live MQL5 bridge, via bridge_bar_flip.BridgeBarFlipTracker
  -- the user chose the bridge over a native recompute so it matches their
  chart exactly): confirmed BULL = "strong" = up, confirmed BEAR = "weak"
  = down. A trap (price between the two lines) is a NON-EVENT: the
  direction just stays wherever the last clear event left it, and only a
  genuine FLIP or TRAP_RESOLVED gives it a fresh timestamp.

  CISD (cisd_bridge.read_cisd() -- the bridge's own STANDING last
  confirmation, not the momentary fresh_cisd()): bullish CISD = up,
  bearish CISD = down.

Ties (identical event_time) favour ATR. If the ATR side has nothing to
offer (its bridge is stale and the tracker has no baseline yet) there is
no bias -- no fallback, no guess. CISD is optional: a missing/stale CISD
bridge just means ATR decides on its own.

Supertrend is deliberately NOT part of this (confirmed: "we see
supertrend later"). Unlike the old structure.py this carries no SL value
-- TM-STR's SL comes from trend_entry.py, not from the bias.

COLD START: the tracker only accumulates history once it is running; a
brand-new one bootstraps "confirmed since NOW", which would hand the ATR
side a fake fresh event time and let it beat an older-but-real CISD.
Seed the tracker's state file once from a live V5S tracker before the
first real run (see project notes) -- do not trust a bias computed from
an empty one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v6_sentinel import cisd_bridge
from v6_sentinel.bridge_bar_flip import BridgeBarFlipTracker

_SOURCE_PRIORITY = {"ATR": 0, "CISD": 1}  # tie-break: lower wins


@dataclass(frozen=True)
class Bias:
    direction: int       # 1 bullish/strong, -1 bearish/weak -- always decisive
    source: str           # "ATR" | "CISD" -- whichever produced the most recent event
    event_time: int        # bar_time of that event


def compute_bias(tracker: BridgeBarFlipTracker, symbol: str, tf_minutes: int) -> Optional[Bias]:
    """None if the ATR side has nothing to offer yet. `tracker` must be
    this SYMBOL's own BridgeBarFlipTracker instance."""
    fs = tracker.update(symbol, tf_minutes)
    if fs is None:
        return None

    # Falls back to confirmed_since_time when last_event is None (the
    # persisted state never saw a flip) -- always a stable timestamp.
    atr_event_time = fs.last_event.bar_time if fs.last_event is not None else fs.confirmed_since_time
    candidates = [(atr_event_time, "ATR", fs.confirmed.value)]

    cisd = cisd_bridge.read_cisd(symbol, tf_minutes)
    if cisd is not None:
        candidates.append((cisd.last_cisd_time, "CISD", cisd_bridge.direction_of(cisd)))

    event_time, source, direction = max(candidates, key=lambda c: (c[0], -_SOURCE_PRIORITY[c[1]]))
    return Bias(direction=direction, source=source, event_time=event_time)
