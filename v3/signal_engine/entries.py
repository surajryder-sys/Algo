"""Entry price / SL calculation for Trend Manager -- v3's own copy of the
market/pullback shape in algo_v2/entries.py, NOT an import: v3 doesn't
share code with algo_v2 in either direction (see CLAUDE.md). Ported
because the underlying algorithm is the right one, with one real change
agreed 2026-08-17: the pullback floor is now a PARAMETER, not a single
hardcoded module constant, because M1 needs its own floor (3) distinct
from M3/M5's (4) -- algo_v2's version can't express that.

Three distinct entry mechanisms, agreed 2026-08-17:

M1  - NEW thresholds, not v2's old M1 logic (which was a pure
      edge+buffer pending, never market/pullback). Distance <=3 -> market.
      3 < distance < 6 -> pending at a 45% pullback, offset-from-edge
      floored at 3. Above 6 -> no trade for this OB.
M3  - unchanged from algo_v2: market <=4, pullback 4 < distance < 12,
      floor 4.
M5  - unchanged from algo_v2: identical numbers to M3, kept as separate
      constants since they're free to diverge again later (same
      rationale as algo_v2's own comment).

Reversal Manager (v3/signal_engine/reversal_manager.py) reuses m3_entry
and m5_entry as-is for its own LTF confirmation, but has its own WIDER
M1 (reversal_m1_entry: market<=4, pullback 4<d<8, floor 4) -- agreed
2026-08-18, deliberately different from Trend Manager's M1 since a
reversal is trying to catch an actual top/bottom and needs more room
not to miss the entry.

Pullback entry is measured as a % giveback of however far price already
ran from the OB edge, floored so it never demands an unreasonably small
giveback just because that run was short -- see compute_entry's own
docstring for the exact mechanism (identical shape to algo_v2's, just
with a per-caller floor).

Initial SL, simplified 2026-08-18 (superseding an earlier, buggier
cross-timeframe "closest edge" version): based ONLY on the OB the trade
actually executed off, using its OPPOSITE edge from the entry edge --
entry sits near a bull OB's TOP (first contact retracing down into it),
but SL sits below that same OB's BOTTOM (protecting against the whole
zone failing, not just the entry edge), buffered by SL_BUFFER (1.0,
user's explicit value). Mirrors algo_v2's own actual convention
(select_sl's candidate edges were documented there as "OB low
(bullish)/OB high (bearish)" -- opposite of its own entry edge) which
this module's own first draft got backwards by reusing ob_edge() for
both purposes; initial_sl() below is the fix.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

SL_BUFFER = 1.0
PULLBACK_PCT = 0.45

# M1 -- new thresholds, agreed 2026-08-17.
M1_MARKET_MAX = 3.0
M1_PULLBACK_MIN = 3.0
M1_PULLBACK_MAX = 6.0
M1_PULLBACK_FLOOR = 3.0

# M3/M5 -- unchanged from algo_v2/entries.py.
M3_MARKET_MAX = 4.0
M3_PULLBACK_MIN = 4.0
M3_PULLBACK_MAX = 12.0
M3_PULLBACK_FLOOR = 4.0

M5_MARKET_MAX = 4.0
M5_PULLBACK_MIN = 4.0
M5_PULLBACK_MAX = 12.0
M5_PULLBACK_FLOOR = 4.0

# Reversal Manager's own M1 confirmation thresholds, agreed 2026-08-18 --
# deliberately WIDER than Trend Manager's M1 (3 / 3-6) per explicit user
# reasoning: "here we might catch a bottom or top, so we keep some space
# buffer, making sure not missing the entry." Floor set equal to
# market_max/pullback_min (4), same internal-consistency pattern as
# Trend Manager's own M1 -- not explicitly restated by the user, but the
# natural extension of the established shape.
REVERSAL_M1_MARKET_MAX = 4.0
REVERSAL_M1_PULLBACK_MIN = 4.0
REVERSAL_M1_PULLBACK_MAX = 8.0
REVERSAL_M1_PULLBACK_FLOOR = 4.0


class EntryMode(Enum):
    NONE = "NONE"
    MARKET = "MARKET"
    PENDING = "PENDING"


@dataclass(frozen=True)
class EntryPlan:
    mode: EntryMode
    entry_price: Optional[float]  # None when mode is MARKET (fills at current price) or NONE


def compute_entry(direction: str, ob_edge: float, current_price: float,
                   market_max: float, pullback_min: float, pullback_max: float,
                   pullback_floor: float, pullback_pct: float = PULLBACK_PCT) -> EntryPlan:
    """direction: "bull" (ob_edge = OB top) or "bear" (ob_edge = OB
    bottom). distance is always measured as how far price has run away
    from the OB edge -- negative (price hasn't reached the edge at all
    yet) means no trade."""
    sign = 1 if direction == "bull" else -1
    distance = (current_price - ob_edge) * sign

    if distance < 0:
        return EntryPlan(EntryMode.NONE, None)
    if distance <= market_max:
        return EntryPlan(EntryMode.MARKET, None)
    if pullback_min < distance < pullback_max:
        # Offset from the OB edge shrinks naturally as distance shrinks
        # (raw offset = distance * (1 - pullback_pct)), floored so a
        # short-distance setup doesn't end up demanding an almost-full
        # giveback just to reach an entry that's already only a couple
        # points off the edge.
        offset = max(distance * (1 - pullback_pct), pullback_floor)
        entry = ob_edge + offset * sign
        return EntryPlan(EntryMode.PENDING, entry)
    return EntryPlan(EntryMode.NONE, None)


def m1_entry(direction: str, ob_edge: float, current_price: float) -> EntryPlan:
    return compute_entry(direction, ob_edge, current_price,
                          M1_MARKET_MAX, M1_PULLBACK_MIN, M1_PULLBACK_MAX, M1_PULLBACK_FLOOR)


def m3_entry(direction: str, ob_edge: float, current_price: float) -> EntryPlan:
    return compute_entry(direction, ob_edge, current_price,
                          M3_MARKET_MAX, M3_PULLBACK_MIN, M3_PULLBACK_MAX, M3_PULLBACK_FLOOR)


def m5_entry(direction: str, ob_edge: float, current_price: float) -> EntryPlan:
    return compute_entry(direction, ob_edge, current_price,
                          M5_MARKET_MAX, M5_PULLBACK_MIN, M5_PULLBACK_MAX, M5_PULLBACK_FLOOR)


def reversal_m1_entry(direction: str, ob_edge: float, current_price: float) -> EntryPlan:
    return compute_entry(direction, ob_edge, current_price,
                          REVERSAL_M1_MARKET_MAX, REVERSAL_M1_PULLBACK_MIN,
                          REVERSAL_M1_PULLBACK_MAX, REVERSAL_M1_PULLBACK_FLOOR)


# Timeframe raw code -> the entry function that applies to it. M15/M30
# never appear here -- they're parent-only, never an execution trigger.
ENTRY_FUNCS = {"1": m1_entry, "3": m3_entry, "5": m5_entry}

# Reversal Manager's own LTF confirmation functions -- M1 uses wider
# thresholds (see reversal_m1_entry), M3/M5 reuse Trend Manager's exact
# same functions ("already prescribed entry logics" -- user's words).
REVERSAL_CONFIRM_FUNCS = {"1": reversal_m1_entry, "3": m3_entry, "5": m5_entry}


def ob_edge(direction: str, top: float, btm: float) -> float:
    """Bull retraces down INTO the zone from above -- first contact is
    the zone's top. Bear retraces up INTO the zone from below -- first
    contact is the zone's bottom. Matches algo_v2's own ob.high/ob.low
    convention exactly."""
    return top if direction == "bull" else btm


def initial_sl(direction: str, top: float, btm: float) -> float:
    """SL based only on the OB the trade actually executed off -- its
    OPPOSITE edge from the entry edge (see module docstring). Bull:
    OB's own bottom, minus buffer. Bear: OB's own top, plus buffer."""
    return (btm - SL_BUFFER) if direction == "bull" else (top + SL_BUFFER)
