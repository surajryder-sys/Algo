"""SMC bias state machine: combines M15/M5 order-block direction into a
Bullish/Bearish call, per the simplified rules for the XAUUSD EA. M3 does
not vote on bias at all -- it only ever checks its own OB against whichever
direction wins here.

Core rule:
  M15 and M5's latest OBs agree            -> full Bullish/Bearish
  they disagree                            -> whichever OB is more recent
                                               wins the direction, labeled
                                               ShortTerm

Both Bullish and Bullish ShortTerm allow the same entries (M1/M3/M5) in the
bullish direction -- ShortTerm is a weaker confirmation, not a smaller set of
allowed sources. The only place the full/ShortTerm distinction matters is
nowhere anymore: a flip to the opposite direction (full or ShortTerm) always
forces the existing opposite-direction position closed, otherwise a stale
position could block the bot from ever entering the new direction until its
own SL/manual close.
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


def compute_bias(m15: TFBias, m5: TFBias) -> BiasResult:
    if m15.direction == 0 and m5.direction == 0:
        return BiasResult(BiasState.NONE, 0)

    if m15.direction == m5.direction:
        state = BiasState.BULLISH if m15.direction == 1 else BiasState.BEARISH
        return BiasResult(state, m15.direction)

    # Disagreement (including one side having no OB yet): recency wins.
    winner_dir = m15.direction if m15.origin_time >= m5.origin_time else m5.direction
    if winner_dir == 0:
        winner_dir = m15.direction or m5.direction

    state = BiasState.BULLISH_SHORTTERM if winner_dir == 1 else BiasState.BEARISH_SHORTTERM
    return BiasResult(state, winner_dir)


def allowed_entry_sources(bias: BiasResult) -> frozenset:
    """Which of {"M1","M3","M5"} may attempt an entry for the current bias
    direction. M15 never executes trades itself (bias-only). Full and
    ShortTerm allow the identical set -- the distinction only matters for
    the force-close rule in management.py."""
    if bias.direction == 0:
        return frozenset()
    return frozenset({"M1", "M3", "M5"})
