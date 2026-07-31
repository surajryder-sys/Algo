"""Order Block + ATR Trail zone rules (V2).

Reads the M5 ATR Trail bridge snapshot and classifies the market as one of:
  STRONG -- last M5 candle closed above the ATR Trail line
  WEAK   -- last M5 candle closed below the ATR Trail line
along with the `event_time` of the most recent Strong<->Weak flip (the ATR
indicator's own trend-buffer flip time, read straight from the bridge).

Eligibility rule (applies to every OB candidate -- M5, M3, and M1 alike):
  STRONG zone -- bullish OBs are always eligible (old or new).
                 Bearish OBs are eligible only if detected at/after the
                 event_time (a fresh bearish OB post-event is allowed to
                 trade even though the zone still reads Strong).
  WEAK zone   -- mirror image: bearish OBs always eligible, bullish OBs
                 only if detected at/after the event_time.
  NONE (no ATR snapshot yet) -- nothing is eligible; fail closed rather
                 than silently ignoring the safeguard.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from atr_bridge.reader import ATRSnapshot


class ZoneState(Enum):
    NONE = "NONE"
    STRONG = "STRONG"
    WEAK = "WEAK"


@dataclass(frozen=True)
class ZoneResult:
    state: ZoneState
    event_time: int


def compute_zone(atr: Optional[ATRSnapshot]) -> ZoneResult:
    if atr is None:
        return ZoneResult(ZoneState.NONE, 0)
    state = ZoneState.STRONG if atr.trend > 0 else ZoneState.WEAK
    return ZoneResult(state, atr.event_time)


def is_eligible(zone: ZoneResult, direction: int, ob_event_time: int) -> bool:
    """direction: 1 bullish, -1 bearish. ob_event_time: the candidate OB's
    own detected/origin time (matches candidates.py's _event_time)."""
    if zone.state == ZoneState.NONE:
        return False

    favored = 1 if zone.state == ZoneState.STRONG else -1
    if direction == favored:
        return True

    # Opposite-of-favored direction: only eligible if it formed at/after the
    # zone's last character-flip event -- older ones stay blocked.
    return ob_event_time >= zone.event_time
