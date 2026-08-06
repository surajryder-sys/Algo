"""Entry price / SL calculation for the FX cross-pairs bot.

Single entry mechanism, single timeframe (H1): a pullback pending order --
never market, never a straight zone-edge order. SL is always the OB zone's
own opposite boundary, not a cross-timeframe edge-minus-buffer like algo_v2's
select_sl() -- there's only one timeframe here, so the zone IS the SL
structure.

Pullback is a 40% giveback of however far price ran from the OB edge before
detection, mirroring algo_v2's m3_or_m5_entry() shape (see algo_v2/entries.py)
but with no market-order tier and no min/max distance gate -- every virgin H1
zone gets a pullback order regardless of how far price has already run.

SL buffer: 0.02% of the SL edge's own price (zone.low for bullish, zone.high
for bearish) -- self-scaling per symbol, so no per-pair pip tuning is needed
across the JPY vs non-JPY crosses in FX_SYMBOLS. Bullish SL = zone.low minus
the buffer; bearish SL = zone.high plus the buffer -- i.e. always pushed
further from price than the raw OB edge, giving a wick through the exact
edge a little room before stopping out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ob_bridge.reader import Zone

PULLBACK_PCT = 0.40
SL_BUFFER_PCT = 0.0002  # 0.02% of the SL edge's own price


@dataclass(frozen=True)
class EntryPlan:
    entry_price: float
    sl: float


def sl_for_zone(direction: int, zone: Zone) -> float:
    """The zone's OTHER boundary (zone.low bullish / zone.high bearish) plus
    a 0.02%-of-that-price buffer, pushed further from price than the raw OB
    edge -- see module docstring. Shared by both entry (pullback_entry) and
    trailing (management.compute_trailing_sl), which recomputes this against
    whatever the *current* latest same-direction zone is as it updates."""
    if direction == 1:
        return zone.low - zone.low * SL_BUFFER_PCT
    return zone.high + zone.high * SL_BUFFER_PCT


def pullback_entry(direction: int, zone: Zone) -> Optional[EntryPlan]:
    """direction: 1 bullish, -1 bearish.

    ob_edge is the zone boundary price ran away from (zone.high for a
    bullish OB, zone.low for a bearish one) -- entry gives back 40% of that
    run, floored at ob_edge itself (never crosses past the edge into the
    zone's far side). SL is sl_for_zone() of this same zone.
    """
    if direction == 1:
        ob_edge = zone.high
        distance = zone.detected_price - ob_edge
    else:
        ob_edge = zone.low
        distance = ob_edge - zone.detected_price

    if distance < 0:
        return None  # detection price hasn't actually run away from the edge yet

    offset_from_edge = distance * (1 - PULLBACK_PCT)
    entry = ob_edge + offset_from_edge if direction == 1 else ob_edge - offset_from_edge

    return EntryPlan(entry_price=entry, sl=sl_for_zone(direction, zone))
