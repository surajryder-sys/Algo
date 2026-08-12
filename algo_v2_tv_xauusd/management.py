"""Position management: trailing SL and the fresh-opposite-OB forced exit
rule. Identical to algo_v2/management.py -- only the OBSnapshot/ATRSnapshot
import swapped for this bot's own reader.py.

Trailing applies uniformly regardless of which timeframe originated the
trade (M1, M3, or M5): always follow whichever of M15/M5/M3's current
same-direction OB edge is closest to the CURRENT price, moving SL only in
the favorable direction (raise for longs, lower for shorts) -- never loosen.

Only one position is ever meant to be open at a time. It force-closes the
instant M5 forms a fresh OPPOSITE-direction OB -- "fresh" meaning its
origin candle postdates the ATR zone's own last Strong<->Weak flip
(atr.event_time). See algo_v2/management.py's docstring for the full
worked-example rationale (unchanged here).
"""
from __future__ import annotations

from typing import Optional

from algo_v2_tv_xauusd.entries import select_sl
from algo_v2_tv_xauusd.reader import ATRSnapshot, OBSnapshot

# Floating-point tolerance for the "did the SL actually improve" check below.
# Same value/reasoning as algo_v2/management.py (confirmed live on the
# USOIL V2 bot, which shares this exact function): real tick sizes (0.01 on
# XAUUSD) are far larger than this epsilon, so any genuine edge move still
# clears it easily.
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
