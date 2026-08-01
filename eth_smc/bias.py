"""SMC bias state machine for the ETHUSD bot: M15 and M30 order-block
direction decide the bias -- M5 no longer votes, it only executes entries
once a direction is set here.

Core rule: whichever of M15/M30 has the most recently-originated OB decides
the direction. This applies even when they already agree -- recency still
determines which one is "the trigger" for stop-loss purposes (see
candidates.py: SL on entry comes from the trigger's own OB edge, not a
closest-search). If the other timeframe agrees, bias is a full Bullish/
Bearish call; if it disagrees, the trigger's direction still stands,
labeled ShortTerm. M5 entries are gated identically in both cases -- the
full/ShortTerm distinction only affects which OB the SL is initially built
from, and that's already pinned down by trigger_tf either way.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BiasState(Enum):
    NONE = "NONE"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    BULLISH_SHORTTERM = "BULLISH_SHORTTERM"
    BEARISH_SHORTTERM = "BEARISH_SHORTTERM"


@dataclass(frozen=True)
class TFBias:
    direction: int    # 1 bullish, -1 bearish, 0 = no OB yet
    origin_time: int  # OB origin (start_time), 0 if no OB yet


@dataclass(frozen=True)
class BiasResult:
    state: BiasState
    direction: int    # 1, -1, or 0
    trigger_tf: str    # "M15" / "M30" / "" if NONE -- the OB entry SL is built from


def compute_bias(m15: TFBias, m30: TFBias) -> BiasResult:
    if m15.direction == 0 and m30.direction == 0:
        return BiasResult(BiasState.NONE, 0, "")

    trigger_tf = "M15" if m15.origin_time >= m30.origin_time else "M30"
    trigger = m15 if trigger_tf == "M15" else m30
    other = m30 if trigger_tf == "M15" else m15

    direction = trigger.direction
    if other.direction == direction:
        state = BiasState.BULLISH if direction == 1 else BiasState.BEARISH
    else:
        state = BiasState.BULLISH_SHORTTERM if direction == 1 else BiasState.BEARISH_SHORTTERM

    return BiasResult(state, direction, trigger_tf)


def allowed_entry_sources(bias: BiasResult) -> frozenset:
    """M5 is the only timeframe that ever executes an entry now -- M15/M30
    are bias-only. Full and ShortTerm allow it identically."""
    if bias.direction == 0:
        return frozenset()
    return frozenset({"M5"})
