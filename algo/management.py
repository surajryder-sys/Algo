"""Position management: trailing SL and the Strong-state forced exit rule.

Trailing applies uniformly regardless of which timeframe originated the
trade (M1, M3, or M5): always follow whichever of M15/M5/M3's current
same-direction OB edge is closest to the CURRENT price, moving SL only in
the favorable direction (raise for longs, lower for shorts) -- never loosen.

Strong bias unconditionally blocks/closes the opposite direction, regardless
of which state (even a ShortTerm-protected coexisting position) opened it.
Medium/ShortTerm/None never force an exit on their own.
"""
from __future__ import annotations

from typing import Optional

from algo.bias import BiasResult, BiasState
from algo.entries import select_sl


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


def bias_flip_exit_direction(bias: BiasResult) -> Optional[int]:
    """Direction (1 or -1) whose open/pending exposure must be closed/blocked
    right now, or None if nothing is forced. Only STRONG triggers this."""
    if bias.state == BiasState.BULLISH_STRONG:
        return -1
    if bias.state == BiasState.BEARISH_STRONG:
        return 1
    return None
