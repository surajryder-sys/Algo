"""M5 Bias -- currently M5/STR (ATR-trail-based) ONLY. M5/ICT
(OB-formation-based) is deferred; once it's added, this module's job
expands to "most recent of the two modes wins" (same pattern
algo_v2/zone.py already uses for its own two--then-three-signal combine).
For now, M5's flip_state.compute() result IS the bias directly: a
confirmed BULL means Strong, BEAR means Weak, and it holds until the
opposite flip -- both flip and trap-resolved-same-side events already
behave correctly for this since flip_state only changes `confirmed` on a
genuine FLIP (a TRAP_RESOLVED event reverts to what was already
confirmed, so bias correctly stays put through one).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v5_sentinel import rates
from v5_sentinel.flip_state import FlipStateResult, compute as compute_flip_state


@dataclass(frozen=True)
class BiasResult:
    direction: int   # 1 Strong (bullish), -1 Weak (bearish)
    since_time: int
    flip_state: FlipStateResult


def compute_m5_bias(symbol: str) -> Optional[BiasResult]:
    series = rates.read_trail_series(symbol, 5)
    if series is None:
        return None
    fs = compute_flip_state(series)
    if fs is None:
        return None
    return BiasResult(direction=fs.confirmed.value, since_time=fs.confirmed_since_time, flip_state=fs)
