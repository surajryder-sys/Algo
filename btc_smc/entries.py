"""Entry price / SL calculation for the BTCUSD SMC bot.

Only one entry mechanism, applied uniformly to all three source timeframes
(M5/M15/M30 -- no M1, no zone+buffer pending style): market order if price
is close enough to the zone, otherwise a 48% pullback entry, otherwise no
trade. Each timeframe has its own market-distance and pullback-range
cutoffs, scaled from the ETHUSD bot's constants (see eth_smc/entries.py) by
BTC's price ratio to ETH (~33.3x at the time these were set) and then
hand-confirmed against BTC's real OB/candle-range behavior.

SL is always OB-structure-based: whichever of M5/M15/M30's current same-
direction OB edge is closest to the entry price, minus/plus a fixed buffer.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

SL_BUFFER = 100.0
PULLBACK_PCT = 0.48

M5_MARKET_MAX = 333.3
M5_PULLBACK_MIN = 333.3
M5_PULLBACK_MAX = 833.3

M15_MARKET_MAX = 333.3
M15_PULLBACK_MIN = 333.3
M15_PULLBACK_MAX = 1000.0

M30_MARKET_MAX = 333.3
M30_PULLBACK_MIN = 333.3
M30_PULLBACK_MAX = 1000.0


class EntryMode(Enum):
    NONE = "NONE"
    MARKET = "MARKET"
    PENDING = "PENDING"


@dataclass(frozen=True)
class EntryPlan:
    mode: EntryMode
    entry_price: Optional[float]  # None when mode is MARKET (fill at send time) or NONE


def market_or_pullback_entry(direction: int, ob_edge: float, detected_price: float,
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
        pullback_amount = distance * pullback_pct
        if direction == 1:
            entry = detected_price - pullback_amount   # == ob_edge + distance*(1-pct)
        else:
            entry = detected_price + pullback_amount
        return EntryPlan(EntryMode.PENDING, entry)

    return EntryPlan(EntryMode.NONE, None)


def m5_entry(direction: int, ob_edge: float, detected_price: float) -> EntryPlan:
    return market_or_pullback_entry(direction, ob_edge, detected_price,
                                    M5_MARKET_MAX, M5_PULLBACK_MIN, M5_PULLBACK_MAX)


def m15_entry(direction: int, ob_edge: float, detected_price: float) -> EntryPlan:
    return market_or_pullback_entry(direction, ob_edge, detected_price,
                                    M15_MARKET_MAX, M15_PULLBACK_MIN, M15_PULLBACK_MAX)


def m30_entry(direction: int, ob_edge: float, detected_price: float) -> EntryPlan:
    return market_or_pullback_entry(direction, ob_edge, detected_price,
                                    M30_MARKET_MAX, M30_PULLBACK_MIN, M30_PULLBACK_MAX)


def select_sl(direction: int, entry_price: float, candidate_edges: dict) -> Optional[float]:
    """candidate_edges: {"M5": edge_or_None, "M15": edge_or_None, "M30": edge_or_None}
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
