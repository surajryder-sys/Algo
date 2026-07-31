"""SMC bias state machine for V2: direction comes solely from M5's own OB
bias -- M15 is intentionally left out of V2 for now (to be reintroduced
later once this is validated against V1). With a single input timeframe
there's no agreement-or-recency logic needed like V1's bias.py has; M5's
bias *is* the bias.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ob_bridge.reader import OBSnapshot


class BiasState(Enum):
    NONE = "NONE"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass(frozen=True)
class BiasResult:
    state: BiasState
    direction: int    # 1, -1, or 0


def compute_bias(m5: Optional[OBSnapshot]) -> BiasResult:
    if m5 is None or m5.bias == 0:
        return BiasResult(BiasState.NONE, 0)

    direction = 1 if m5.bias > 0 else -1
    state = BiasState.BULLISH if direction == 1 else BiasState.BEARISH
    return BiasResult(state, direction)


def allowed_entry_sources(bias: BiasResult) -> frozenset:
    """Which of {"M1","M3","M5"} may attempt an entry for the current bias
    direction. M15 never executes trades itself in V1 either -- unchanged
    here, just without M15 voting on direction."""
    if bias.direction == 0:
        return frozenset()
    return frozenset({"M1", "M3", "M5"})
