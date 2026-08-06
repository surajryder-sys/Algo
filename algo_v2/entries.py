"""Entry price / SL calculation for the XAUUSD SMC EA.

Three distinct entry mechanisms, per timeframe:

M1  - straight pending order directly on the zone edge, with a small buffer
      added to the entry price itself (never market, never a pullback).
M3  - market order if close enough to the zone, otherwise a 45% pullback
      entry, otherwise no trade (never zone+buffer).
M5  - same shape as M3; market/pullback thresholds currently match M3
      exactly (both market_max=4.0, pullback_min=4.0, pullback_max=12.0),
      kept as separate constants since they're free to diverge again later.

Pullback entry is measured as a % giveback of however far price already ran
from the OB edge, floored so it never demands an unreasonably small giveback
just because that run was short: the entry's offset from the OB edge itself
never sits closer than PULLBACK_MIN_EDGE_OFFSET, even when pullback_pct's
raw offset (distance * (1 - pullback_pct)) would put it closer. Below that
floor's crossover distance, entries collapse toward a flat, shallow
giveback instead of scaling down with distance -- confirmed by design: a
short run from the edge shouldn't need almost as deep a retracement (in %
terms) as a long one just to fill.

SL is always OB-structure-based: whichever of M15/M5/M3's current same-
direction OB edge is closest to the entry price, minus/plus a fixed buffer.
That buffer (0.5) is the same for every timeframe's SL. M1's entry buffer
(0.25) is unrelated and only ever applies to M1's own entry price.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

SL_BUFFER = 0.5
M1_ENTRY_BUFFER = 0.25
PULLBACK_PCT = 0.45
PULLBACK_MIN_EDGE_OFFSET = 4.0  # entry never sits closer than this to the OB
                                # edge itself -- see module docstring

M3_MARKET_MAX = 4.0  # matches M3_PULLBACK_MIN now -- no more 3.0-4.0 dead zone
M3_PULLBACK_MIN = 4.0
M3_PULLBACK_MAX = 12.0

M5_MARKET_MAX = 4.0
M5_PULLBACK_MIN = 4.0
M5_PULLBACK_MAX = 12.0


class EntryMode(Enum):
    NONE = "NONE"
    MARKET = "MARKET"
    PENDING = "PENDING"


@dataclass(frozen=True)
class EntryPlan:
    mode: EntryMode
    entry_price: Optional[float]  # None when mode is MARKET (fill at send time) or NONE


def m3_or_m5_entry(direction: int, ob_edge: float, detected_price: float,
                    market_max: float, pullback_min: float, pullback_max: float,
                    pullback_pct: float = PULLBACK_PCT) -> EntryPlan:
    """direction: 1 bullish (ob_edge = ob.high), -1 bearish (ob_edge = ob.low).
    distance is always measured as how far price ran away from the zone edge."""
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
        # already only a couple points off the edge. Always < distance
        # itself here (distance > pullback_min == the floor value), so
        # entry never crosses past detected_price.
        offset_from_edge = max(distance * (1 - pullback_pct), PULLBACK_MIN_EDGE_OFFSET)
        if direction == 1:
            entry = ob_edge + offset_from_edge
        else:
            entry = ob_edge - offset_from_edge
        return EntryPlan(EntryMode.PENDING, entry)

    return EntryPlan(EntryMode.NONE, None)


def m3_entry(direction: int, ob_edge: float, detected_price: float) -> EntryPlan:
    return m3_or_m5_entry(direction, ob_edge, detected_price,
                           M3_MARKET_MAX, M3_PULLBACK_MIN, M3_PULLBACK_MAX)


def m5_entry(direction: int, ob_edge: float, detected_price: float) -> EntryPlan:
    return m3_or_m5_entry(direction, ob_edge, detected_price,
                           M5_MARKET_MAX, M5_PULLBACK_MIN, M5_PULLBACK_MAX)


def m1_entry_price(direction: int, ob_edge: float) -> float:
    """Straight pending order on the zone edge, M1-specific buffer."""
    return ob_edge + M1_ENTRY_BUFFER if direction == 1 else ob_edge - M1_ENTRY_BUFFER


def select_sl(direction: int, entry_price: float, candidate_edges: dict) -> Optional[float]:
    """candidate_edges: {"M15": edge_or_None, "M5": edge_or_None, "M3": edge_or_None}
    where each edge is that timeframe's current same-direction OB low (bullish)
    or OB high (bearish). Picks whichever is closest to entry_price, but only
    among edges on the geometrically valid side of entry -- below entry for a
    buy, above entry for a sell. An edge on the wrong side would produce a
    backwards SL (broker-rejected as invalid stops) and must never be chosen
    just for being numerically closest."""
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
