"""Parent bias -- REVISED 2026-09-10: M15 removed COMPLETELY as a parent.
User's own words: "lets remove completely M15 as parent, follow m5 as
parent, and m3 as execution." This retires the 2026-09-07 M5-primary/
M15-fallback design (M15 voting only when M5 itself was trapped), which
itself had retired the original 2026-09-03 design (M5/M15 as equal
co-parents, disagreement opening both directions). M5/ICT (OB-formation-
based) is still deferred; M5's own bias is STR-only (ATR trail
flip_state), same mechanism as M3 execution just on its own data.

Rule now (confirmed with the user, 2026-09-10):

  M5 clear (either direction) -> follow M5 alone.
  M5 trapped                  -> NEITHER direction allowed, full stop --
                                   wait for M5 itself to resolve one way
                                   or the other. No fallback vote from
                                   any other timeframe.

"Trapped" means M5's OWN flip_state is currently in the watching/
ambiguous phase (FlipStateResult.watching is not None) -- regardless of
what its `confirmed` value still reads, since a trapped parent's
confirmed direction is exactly the stale value under question, not a
reliable vote.

M15's own flip_state is still computed and carried on ParentBiasResult
(and still shown in decision_log's m15_label field) purely for
diagnostic/logging visibility -- e.g. explaining a chart that doesn't
visually look like it matches M5's own bias. It has ZERO effect on
bull_allowed/bear_allowed any more; nothing in main.py reads M15 for a
gating decision.

2026-09-07: switched from copy_rates+bridge-tie-breaker to BRIDGE-ONLY
(bridge_bar_flip.BridgeBarFlipTracker) -- user's explicit direction:
"remove dependancy of copy rates for M5,M3,M1 -- follow exactly bridge,
nothing else... not just for RM, even for TM, it should use bridge
data." Still strictly bar-close-gated ("strictly on bar close even on
bridge data") -- copy_rates is used ONLY to detect when a bar closed and
what its close price was (bridge_bar_flip.read_last_closed_bar, no
ATR/trail computation of our own at all); the LINE VALUES tested against
that close come from the bridge. If a parent's bridge data is missing/
stale right when its bar closes, that bar is simply skipped (state stays
as it was) -- no fallback, no guess. See bridge_bar_flip.py's own
docstring for the full mechanism and why (the old copy_rates recompute
was a real, confirmed drift source, same root cause already fixed for
M3 via the bridge tie-breaker -- this just removes the recompute
entirely instead of only overriding it on disagreement).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.flip_state import Confirmed, FlipStateResult


@dataclass(frozen=True)
class BiasResult:
    """Kept for M5-only diagnostics/status checks -- main.py's entry gate
    uses compute_parent_bias() below instead."""
    direction: int   # 1 Strong (bullish), -1 Weak (bearish)
    since_time: int
    flip_state: FlipStateResult


def compute_m5_bias(tracker: BridgeBarFlipTracker, symbol: str) -> Optional[BiasResult]:
    fs = tracker.update(symbol, 5)
    if fs is None:
        return None
    return BiasResult(direction=fs.confirmed.value, since_time=fs.confirmed_since_time, flip_state=fs)


@dataclass(frozen=True)
class ParentBiasResult:
    bull_allowed: bool
    bear_allowed: bool
    m5: FlipStateResult
    m15: FlipStateResult   # diagnostics/logging only, 2026-09-10 -- never gates bull_allowed/bear_allowed
    source: str   # "M5" / "M5_TRAPPED" -- informational, for logging/tagging

    def allows(self, direction: int) -> bool:
        return self.bull_allowed if direction == 1 else self.bear_allowed


def compute_parent_bias(tracker: BridgeBarFlipTracker, symbol: str) -> Optional[ParentBiasResult]:
    fs5 = tracker.update(symbol, 5)
    fs15 = tracker.update(symbol, 15)  # still fetched -- diagnostics only, see module docstring
    if fs5 is None or fs15 is None:
        return None

    if fs5.watching is None:
        bull = fs5.confirmed == Confirmed.BULL
        bear = fs5.confirmed == Confirmed.BEAR
        source = "M5"
    else:
        bull = False
        bear = False
        source = "M5_TRAPPED"

    return ParentBiasResult(bull_allowed=bull, bear_allowed=bear, m5=fs5, m15=fs15, source=source)
