"""Entry price / SL calculation for the standalone USOIL SMC V2 bot.

Preserved snapshot -- see config.py's docstring. Two entry mechanisms (M5,
M15 -- M30 still to come), both the same shape: market order if within
MARKET_MAX of the zone edge, a shallow pullback entry if between
PULLBACK_MIN and PULLBACK_MAX, otherwise no trade. M15 reuses M5's exact
numbers.

Pullback entry is measured as a % giveback of however far price already
ran from the OB edge, floored so it never demands an unreasonably small
giveback just because that run was short: the entry's offset from the OB
edge itself never sits closer than PULLBACK_MIN_EDGE_OFFSET, even when
pullback_pct's raw offset (distance * (1 - pullback_pct)) would put it
closer. Ported from the same fix on algo_v2/entries.py (XAUUSD), where the
floor is set equal to PULLBACK_MIN itself; USOIL's floor follows the same
pattern (0.600, matching both tiers' own PULLBACK_MIN).

SL is OB-structure-based: whichever of M5/M15's current same-direction OB
edge is closest to the entry price, minus/plus a fixed buffer -- same
buffer for every tier.

These are absolute USOIL price distances (not points), given directly --
deliberately NOT copied from algo_v2/entries.py's XAUUSD values, which are
tuned for gold's very different price scale (~$2400+ vs. USOIL's ~$60-90).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

SL_BUFFER = 0.100
PULLBACK_PCT = 0.45
PULLBACK_MIN_EDGE_OFFSET = 0.600  # entry never sits closer than this to the
                                  # OB edge itself -- see module docstring

M5_MARKET_MAX = 0.600
M5_PULLBACK_MIN = 0.600
M5_PULLBACK_MAX = 0.900

M15_MARKET_MAX = 0.600
M15_PULLBACK_MIN = 0.600
M15_PULLBACK_MAX = 0.900


class EntryMode(Enum):
    NONE = "NONE"
    MARKET = "MARKET"
    PENDING = "PENDING"


@dataclass(frozen=True)
class EntryPlan:
    mode: EntryMode
    entry_price: Optional[float]  # None when mode is MARKET (fill at send time) or NONE


def _tiered_entry(direction: int, ob_edge: float, detected_price: float,
                  market_max: float, pullback_min: float, pullback_max: float,
                  pullback_pct: float = PULLBACK_PCT) -> EntryPlan:
    """direction: 1 bullish (ob_edge = ob.high), -1 bearish (ob_edge = ob.low).
    distance is always measured as how far price ran away from the zone edge.
    Shared by every tier -- each tier's build_<tf>_candidate just supplies
    its own market_max/pullback_min/pullback_max."""
    if direction == 1:
        distance = detected_price - ob_edge
    else:
        distance = ob_edge - detected_price

    if distance < 0:
        return EntryPlan(EntryMode.NONE, None)

    if distance <= market_max:
        return EntryPlan(EntryMode.MARKET, None)

    if pullback_min < distance < pullback_max:
        # Offset from the OB edge shrinks naturally as distance shrinks
        # (raw offset = distance * (1 - pullback_pct)); floored at
        # PULLBACK_MIN_EDGE_OFFSET so short-distance setups don't end up
        # demanding an almost-full giveback just to reach an entry that's
        # already only a sliver off the edge. Always < distance itself here
        # (distance > pullback_min == the floor value), so entry never
        # crosses past detected_price.
        offset_from_edge = max(distance * (1 - pullback_pct), PULLBACK_MIN_EDGE_OFFSET)
        if direction == 1:
            entry = ob_edge + offset_from_edge
        else:
            entry = ob_edge - offset_from_edge
        return EntryPlan(EntryMode.PENDING, entry)

    return EntryPlan(EntryMode.NONE, None)


def m5_entry(direction: int, ob_edge: float, detected_price: float) -> EntryPlan:
    return _tiered_entry(direction, ob_edge, detected_price,
                         M5_MARKET_MAX, M5_PULLBACK_MIN, M5_PULLBACK_MAX)


def m15_entry(direction: int, ob_edge: float, detected_price: float) -> EntryPlan:
    return _tiered_entry(direction, ob_edge, detected_price,
                         M15_MARKET_MAX, M15_PULLBACK_MIN, M15_PULLBACK_MAX)


def select_sl(direction: int, entry_price: float, candidate_edges: dict) -> Optional[float]:
    """candidate_edges: {"M5": edge_or_None, "M15": edge_or_None}. Picks
    whichever edge is closest to entry_price, but only among edges on the
    geometrically valid side of entry -- below entry for a buy, above
    entry for a sell. An edge on the wrong side would produce a backwards
    SL (broker-rejected as invalid stops) and must never be chosen just
    for being numerically closest."""
    valid_side = {
        tf: edge for tf, edge in candidate_edges.items()
        if edge is not None and ((direction == 1 and edge < entry_price) or
                                  (direction == -1 and edge > entry_price))
    }
    if not valid_side:
        return None

    closest_tf = min(valid_side, key=lambda tf: abs(valid_side[tf] - entry_price))
    edge = valid_side[closest_tf]
    return edge - SL_BUFFER if direction == 1 else edge + SL_BUFFER
