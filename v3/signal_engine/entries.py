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

Pullback entry is measured as a % giveback of however far price already
ran from the OB edge, floored so it never demands an unreasonably small
giveback just because that run was short -- see compute_entry's own
docstring for the exact mechanism (identical shape to algo_v2's, just
with a per-caller floor).

SL is always OB-structure-based: whichever candidate timeframe's current
same-direction OB edge is closest to the entry price (only counting
edges on the geometrically valid side), minus/plus a fixed buffer (0.5,
same as algo_v2).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

SL_BUFFER = 0.5
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


# Timeframe raw code -> the entry function that applies to it. M15/M30
# never appear here -- they're parent-only, never an execution trigger.
ENTRY_FUNCS = {"1": m1_entry, "3": m3_entry, "5": m5_entry}


def ob_edge(direction: str, top: float, btm: float) -> float:
    """Bull retraces down INTO the zone from above -- first contact is
    the zone's top. Bear retraces up INTO the zone from below -- first
    contact is the zone's bottom. Matches algo_v2's own ob.high/ob.low
    convention exactly."""
    return top if direction == "bull" else btm


def select_sl(direction: str, entry_price: float, candidate_edges: Dict[str, Optional[float]]) -> Optional[float]:
    """candidate_edges: {timeframe_label: edge_or_None}, each edge being
    that timeframe's current same-direction OB edge. Picks whichever is
    closest to entry_price, but only among edges on the geometrically
    valid side of entry -- below entry for a buy, above entry for a
    sell. An edge on the wrong side would produce a backwards SL
    (broker-rejected as invalid stops) and must never be chosen just for
    being numerically closest."""
    valid = {
        tf: edge for tf, edge in candidate_edges.items()
        if edge is not None and ((direction == "bull" and edge < entry_price) or
                                  (direction == "bear" and edge > entry_price))
    }
    if not valid:
        return None
    closest_tf = min(valid, key=lambda tf: abs(valid[tf] - entry_price))
    edge = valid[closest_tf]
    return edge - SL_BUFFER if direction == "bull" else edge + SL_BUFFER
