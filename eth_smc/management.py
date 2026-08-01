"""Position management: trailing SL and the bias-flip forced exit rule.

Every position is opened by M5, but trailing only ever follows M15/M30
(M5 is never a SL source -- see entries.py): each poll, re-evaluate
whichever of M15/M30's current same-direction OB edge is closest to the
CURRENT price, moving SL only in the favorable direction (raise for longs,
lower for shorts) -- never loosen. This can switch which timeframe is
supplying the SL over time, unlike the fixed trigger used at entry.

Only one position is ever meant to be open at a time, matching the current
bias direction. Any bias direction (full or ShortTerm) unconditionally
forces the opposite-direction position/pending order closed -- otherwise a
stale position could sit there blocking new entries in the now-correct
direction until its own SL or a manual close, which defeats the point of
having a single live bias.
"""
from __future__ import annotations

from typing import Optional

from eth_smc.bias import BiasResult
from eth_smc.entries import select_sl


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
    """Direction (1 or -1) whose open/pending exposure must be closed/
    cancelled right now, or None if bias has no direction. Any bias
    direction -- STRONG, MEDIUM, or SHORTTERM -- forces the opposite side
    out."""
    if bias.direction == 1:
        return -1
    if bias.direction == -1:
        return 1
    return None
