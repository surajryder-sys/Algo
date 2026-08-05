"""Position management: trailing SL and the fresh-opposite-OB forced exit
rule.

Trailing applies uniformly regardless of which timeframe originated the
trade (M1, M3, or M5): always follow whichever of M15/M5/M3's current
same-direction OB edge is closest to the CURRENT price, moving SL only in
the favorable direction (raise for longs, lower for shorts) -- never loosen.

Only one position is ever meant to be open at a time. It force-closes the
instant M5 forms a fresh OPPOSITE-direction OB -- "fresh" meaning its
origin candle postdates the ATR zone's own last Strong<->Weak flip
(atr.event_time). Confirmed live and by spec: holding a position through a
zone flip alone is fine (e.g. SELL open, zone flips Weak->Strong -- keep
holding); only a genuinely fresh opposite M5 OB that forms AFTER that flip
should force it closed, or the position's own SL. An M5 OB that predates
the flip is stale and must NOT trigger a close, even if it's M5's own
most-recent-by-raw-comparison OB in that direction -- that comparison
alone was the bug (see git history): M5 can go a long time without a new
OB on one side, leaving a stale old one that's technically "the latest"
but has nothing to do with what's happened since.
"""
from __future__ import annotations

from typing import Optional

from atr_bridge.reader import ATRSnapshot
from ob_bridge.reader import OBSnapshot
from algo_v2.entries import select_sl


def compute_trailing_sl(direction: int, current_price: float, current_sl: Optional[float],
                        candidate_edges: dict) -> Optional[float]:
    """Returns a new SL only if it moves in the favorable direction; None if
    no update should be made (no closer/appropriate OB, or it would loosen)."""
    proposed = select_sl(direction, current_price, candidate_edges)
    if proposed is None:
        return None

    if current_sl is None:
        return proposed

    if direction == 1 and proposed > current_sl:
        return proposed
    if direction == -1 and proposed < current_sl:
        return proposed
    return None


def fresh_opposite_ob_exists(m5: Optional[OBSnapshot], atr: Optional[ATRSnapshot],
                             position_direction: int) -> bool:
    """True if M5 has formed an OB opposite to position_direction whose
    origin candle (start_time) postdates the ATR zone's own last flip
    (atr.event_time) -- the sole trigger for force-closing an open
    position. position_direction: 1 for an open BUY, -1 for an open SELL
    (checks the opposite side)."""
    if m5 is None or atr is None:
        return False

    opposite = -position_direction
    history = m5.bull if opposite == 1 else m5.bear
    if not history:
        return False

    return history[0].start_time > atr.event_time
