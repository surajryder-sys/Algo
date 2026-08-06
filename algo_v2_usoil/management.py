"""Position management: trailing SL and the fresh-opposite-OB forced exit
rule.

Trailing follows whichever of M5/M15's current same-direction OB edge is
closest to the current price, moving SL only in the favorable direction
(raise for longs, lower for shorts) -- never loosen. Unchanged by the
M15-is-the-anchor switch (see zone.py) -- both tiers' edges feed SL
selection regardless of which one originated the trade or which one is the
zone anchor.

Only one position is ever meant to be open at a time. It force-closes the
instant M15 -- the zone anchor -- forms a fresh OPPOSITE-direction OB,
because that's what actually flips (or is about to flip) the bias itself;
"fresh" meaning its origin candle postdates the ATR zone's own last
Strong<->Weak flip (atr.event_time). Deliberately M15-only, not M5: an
opposite M5 OB doesn't move the anchor and so doesn't by itself invalidate
why the position was opened (see algo_v2/management.py's docstring for the
same reasoning applied to XAUUSD's M5 anchor)."""
from __future__ import annotations

from typing import Optional

from atr_bridge.reader import ATRSnapshot
from ob_bridge.reader import OBSnapshot
from algo_v2_usoil.entries import select_sl


# Floating-point tolerance for the "did the SL actually improve" check below.
# Confirmed live: `edge - SL_BUFFER` (e.g. 74.921 - 0.1) doesn't always land on
# exactly 74.821 in IEEE floats -- it can come out ~1e-14 off (74.82100000000001).
# Comparing that against a broker-reported current_sl of exactly 74.821 with a
# bare `>` reads as "still improving" forever, even though the underlying OB
# edge never moved -- one position logged 27,000+ consecutive identical
# [TRAIL] prints/broker modify calls this way. Real tick sizes (0.001 on
# USOIL, 0.01 on XAUUSD) are far larger than this epsilon, so any genuine
# edge move still clears it easily.
_MIN_SL_IMPROVEMENT = 1e-6


def compute_trailing_sl(direction: int, current_price: float, current_sl: Optional[float],
                        candidate_edges: dict) -> Optional[float]:
    """Returns a new SL only if it moves in the favorable direction by more
    than floating-point noise; None if no update should be made (no closer/
    appropriate OB, or it would loosen)."""
    proposed = select_sl(direction, current_price, candidate_edges)
    if proposed is None:
        return None

    if current_sl is None:
        return proposed

    if direction == 1 and proposed > current_sl + _MIN_SL_IMPROVEMENT:
        return proposed
    if direction == -1 and proposed < current_sl - _MIN_SL_IMPROVEMENT:
        return proposed
    return None


def fresh_opposite_ob_exists(m15: Optional[OBSnapshot], atr: Optional[ATRSnapshot],
                             position_direction: int) -> bool:
    """True if M15 (the zone anchor) has formed an OB opposite to
    position_direction whose origin candle (start_time) postdates the ATR
    zone's own last flip (atr.event_time) -- the sole trigger for
    force-closing an open position. position_direction: 1 for an open BUY,
    -1 for an open SELL (checks the opposite side)."""
    if m15 is None or atr is None:
        return False

    opposite = -position_direction
    history = m15.bull if opposite == 1 else m15.bear
    if not history:
        return False

    return history[0].start_time > atr.event_time
