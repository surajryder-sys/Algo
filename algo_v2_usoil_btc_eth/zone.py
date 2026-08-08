"""Order Block + ATR Trail zone rules (V2, merged USOIL+BTCUSD+ETHUSD bot).

Fully symbol-agnostic -- takes whichever symbol's M15/ATR snapshots the
caller passes in, one call per symbol per poll (see main.py). M15 is the
zone anchor for every symbol here (both the ATR Trail and the OB-flip
inputs below are M15-based) -- NOT M5. This is the opposite of algo_v2
(XAUUSD), where M5 is the anchor; the roles are deliberately swapped here
per spec. The "effective direction" and its event_time boundary are the
most recent of THREE competing signals, all M15-based:
  1. M15 forming its own fresh bullish OB (origin candle time)
  2. M15 forming its own fresh bearish OB (origin candle time)
  3. the ATR Trail's last Strong<->Weak flip (also M15-based -- the
     indicator is attached to the M15 USOIL chart, not M5, specifically so
     this flip lines up with the same timeframe as the OB inputs above)

Whichever of those three timestamps is the most recent wins: its direction
becomes the effective direction (STRONG=bullish-favored, WEAK=bearish-
favored), and its timestamp becomes the event_time boundary the M5 entry
candidate gets checked against (M5 is now the strict subordinate tier --
see main.py).

Eligibility rule (M15, the zone's own input):
  Direction matching the effective direction -- always eligible, old or
  new.
  Direction opposite the effective direction -- eligible only if that
  specific OB's own event time is at/after the event_time boundary.
  No effective direction yet (no data at all) -- nothing eligible; fail
  closed rather than silently ignoring the safeguard.

M5 uses the same function with strict=True: it only trades when its
direction ALSO matches the effective direction right now -- no "postdates
the boundary" exception. This mirrors M1/M3's role on algo_v2 (XAUUSD),
just with M5 filling that subordinate-strict-tier role instead of the
zone-anchor role it had before this change.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from atr_bridge.reader import ATRSnapshot
from ob_bridge.reader import OBSnapshot


class ZoneState(Enum):
    NONE = "NONE"
    STRONG = "STRONG"
    WEAK = "WEAK"


@dataclass(frozen=True)
class ZoneResult:
    state: ZoneState     # STRONG = effective direction bullish, WEAK = bearish, NONE = no data yet
    event_time: int      # the winning (most recent) of the three source timestamps


def _ob_time(m15: Optional[OBSnapshot], direction: int) -> int:
    """M15's own latest OB origin time in this direction, 0 if none. Uses
    start_time (the OB's origin candle), not detected_time -- confirmed
    live that detected_time can jitter by a second or two for the same
    zone, which would make this event_time unstable; start_time never
    changes for a given rectangle (same fix already applied in
    candidates.py's _event_time)."""
    if m15 is None:
        return 0
    history = m15.bull if direction == 1 else m15.bear
    if not history:
        return 0
    return history[0].start_time


def compute_zone(atr: Optional[ATRSnapshot], m15: Optional[OBSnapshot]) -> ZoneResult:
    """Combines the (M15) ATR Trail flip with M15's own latest bullish/
    bearish OB times -- whichever of the three is most recent sets the
    effective direction and event_time boundary."""
    candidates = []  # (event_time, direction)

    if atr is not None:
        candidates.append((atr.event_time, 1 if atr.trend > 0 else -1))

    bull_time = _ob_time(m15, 1)
    if bull_time > 0:
        candidates.append((bull_time, 1))

    bear_time = _ob_time(m15, -1)
    if bear_time > 0:
        candidates.append((bear_time, -1))

    if not candidates:
        return ZoneResult(ZoneState.NONE, 0)

    event_time, direction = max(candidates, key=lambda c: c[0])
    state = ZoneState.STRONG if direction == 1 else ZoneState.WEAK
    return ZoneResult(state, event_time)


def is_eligible(zone: ZoneResult, direction: int, ob_event_time: int, strict: bool = False) -> bool:
    """direction: 1 bullish, -1 bearish. ob_event_time: the candidate OB's
    own detected/origin time (matches candidates.py's _event_time).
    strict: M5 only -- drops the "opposite but newer" exception below; the
    candidate's direction must equal the effective direction, full stop.
    M15 always calls this with the lenient default (it's the zone's own
    input, so the exception is effectively inert for it anyway)."""
    if zone.state == ZoneState.NONE:
        return False

    favored = 1 if zone.state == ZoneState.STRONG else -1
    if direction == favored:
        return True

    if strict:
        return False

    # Opposite-of-effective-direction: only eligible if it formed at/after
    # the current event_time boundary -- older ones stay blocked.
    return ob_event_time >= zone.event_time
