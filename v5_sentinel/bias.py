"""Parent bias -- M5 AND M15 both act as parents now (2026-09-03 design
change; M15 was declared as "Structure" at the very start of this build
but had no active rule until now). M5/ICT (OB-formation-based) is still
deferred for both timeframes; each parent's own bias is STR-only (ATR
trail flip_state), same mechanism as M3 execution just on its own data.

Decision table (confirmed with the user, including the deliberate
no-tie-break-on-disagreement behavior):

  M5 clear, M15 clear, SAME direction  -> only that direction allowed
  M5 clear, M15 clear, DIFFERENT dir   -> BOTH directions allowed (no
                                           tie-break between them -- a
                                           parent disagreement never
                                           picks a winner, it just
                                           doesn't veto either side)
  M5 trapped, M15 clear                -> follow M15 alone, ignore M5
  M15 trapped, M5 clear                -> follow M5 alone, ignore M15
                                           (this was already today's
                                           M5-only behavior before M15
                                           became a parent)
  Both trapped                         -> BOTH directions allowed
                                           (neither parent can veto)

"Trapped" means that parent's OWN flip_state is currently in the
watching/ambiguous phase (FlipStateResult.watching is not None) --
regardless of what its `confirmed` value still reads, since a trapped
parent's confirmed direction is exactly the stale value under question,
not a reliable vote.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v5_sentinel import rates
from v5_sentinel.flip_state import Confirmed, FlipStateResult, compute as compute_flip_state


@dataclass(frozen=True)
class BiasResult:
    """Kept for M5-only diagnostics/status checks -- main.py's entry gate
    uses compute_parent_bias() below instead."""
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


@dataclass(frozen=True)
class ParentBiasResult:
    bull_allowed: bool
    bear_allowed: bool
    m5: FlipStateResult
    m15: FlipStateResult
    source: str   # "AGREE" / "DISAGREE" / "M5_ONLY" / "M15_ONLY" / "BOTH_TRAPPED" -- informational, for logging only

    def allows(self, direction: int) -> bool:
        return self.bull_allowed if direction == 1 else self.bear_allowed


def compute_parent_bias(symbol: str) -> Optional[ParentBiasResult]:
    m5_series = rates.read_trail_series(symbol, 5)
    m15_series = rates.read_trail_series(symbol, 15)
    if m5_series is None or m15_series is None:
        return None
    fs5 = compute_flip_state(m5_series)
    fs15 = compute_flip_state(m15_series)
    if fs5 is None or fs15 is None:
        return None

    m5_trapped = fs5.watching is not None
    m15_trapped = fs15.watching is not None

    if m5_trapped and not m15_trapped:
        bull = fs15.confirmed == Confirmed.BULL
        bear = fs15.confirmed == Confirmed.BEAR
        source = "M15_ONLY"
    elif m15_trapped and not m5_trapped:
        bull = fs5.confirmed == Confirmed.BULL
        bear = fs5.confirmed == Confirmed.BEAR
        source = "M5_ONLY"
    elif m5_trapped and m15_trapped:
        bull = True
        bear = True
        source = "BOTH_TRAPPED"
    else:
        bull = (fs5.confirmed == Confirmed.BULL) or (fs15.confirmed == Confirmed.BULL)
        bear = (fs5.confirmed == Confirmed.BEAR) or (fs15.confirmed == Confirmed.BEAR)
        source = "AGREE" if fs5.confirmed == fs15.confirmed else "DISAGREE"

    return ParentBiasResult(bull_allowed=bull, bear_allowed=bear, m5=fs5, m15=fs15, source=source)
