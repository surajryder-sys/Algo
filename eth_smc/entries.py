"""Entry price / SL calculation for the ETHUSD SMC bot.

Only M5 ever executes an entry now (see bias.py -- M15/M30 are bias-only).
Market order if price is close enough to the M5 zone at the moment it was
detected, otherwise a 48% pullback entry, otherwise no trade.

Stop-loss has two distinct paths, deliberately not the same function:
  - On entry: fixed to whichever of M15/M30 WON the bias (bias.trigger_tf)
    -- see sl_from_edge(). Deterministic, not a "closest" search; the
    trigger IS the SL source.
  - While trailing an open position: re-evaluated every poll as whichever
    of M15/M30's CURRENT same-direction edge is closest to current price
    (select_sl()) -- this can switch from one to the other as price moves,
    unlike the fixed trigger used at entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

SL_BUFFER = 3.0
PULLBACK_PCT = 0.45

M5_MARKET_MAX = 4.0
M5_PULLBACK_MIN = 4.0
M5_PULLBACK_MAX = 20.0


class EntryMode(Enum):
    NONE = "NONE"
    MARKET = "MARKET"
    PENDING = "PENDING"


@dataclass(frozen=True)
class EntryPlan:
    mode: EntryMode
    entry_price: Optional[float]  # None when mode is MARKET (fill at send time) or NONE


def m5_entry(direction: int, ob_edge: float, detected_price: float) -> EntryPlan:
    """direction: 1 bullish (ob_edge = ob.high), -1 bearish (ob_edge = ob.low).
    distance is always measured as how far price ran away from the zone edge."""
    if direction == 1:
        distance = detected_price - ob_edge
    else:
        distance = ob_edge - detected_price

    if distance < 0:
        return EntryPlan(EntryMode.NONE, None)

    if distance <= M5_MARKET_MAX:
        return EntryPlan(EntryMode.MARKET, None)

    if M5_PULLBACK_MIN < distance < M5_PULLBACK_MAX:
        pullback_amount = distance * PULLBACK_PCT
        if direction == 1:
            entry = detected_price - pullback_amount   # == ob_edge + distance*(1-pct)
        else:
            entry = detected_price + pullback_amount
        return EntryPlan(EntryMode.PENDING, entry)

    return EntryPlan(EntryMode.NONE, None)


def sl_from_edge(direction: int, edge: float) -> float:
    """Fixed SL for a brand-new entry: the trigger timeframe's own current
    same-direction OB edge, deterministic -- not a search among candidates."""
    return edge - SL_BUFFER if direction == 1 else edge + SL_BUFFER


def select_sl(direction: int, current_price: float, candidate_edges: dict) -> Optional[float]:
    """Trailing only. candidate_edges: {"M15": edge_or_None, "M30": edge_or_None}
    where each edge is that timeframe's current same-direction OB low (bullish)
    or OB high (bearish). Picks whichever is closest to current_price, but only
    among edges on the geometrically valid side of price -- below price for a
    long, above for a short. An edge on the wrong side would produce a
    backwards SL (broker-rejected as invalid stops) and must never be chosen
    just for being numerically closest."""
    valid_side = {
        tf: edge for tf, edge in candidate_edges.items()
        if edge is not None and ((direction == 1 and edge < current_price) or
                                  (direction == -1 and edge > current_price))
    }
    if not valid_side:
        return None

    closest_tf = min(valid_side, key=lambda tf: abs(valid_side[tf] - current_price))
    edge = valid_side[closest_tf]
    return edge - SL_BUFFER if direction == 1 else edge + SL_BUFFER
