"""Zone-ranking helpers for take-profit target selection and trailing
stop-loss placement, operating on the same bridge zones zone_watcher tracks.
"""
from __future__ import annotations

from dataclasses import dataclass

from ob_mtf_bot.bridge_reader import BridgeState


@dataclass(frozen=True)
class ZoneRef:
    zone_type: str    # "OB" or "FVG"
    tf: str
    direction: int     # 1 bullish, -1 bearish
    low: float
    high: float
    identity: str      # OB signature or FVG name - matches zone_watcher's keys
    tested: bool        # virgin=False (OB) or retested=True (FVG)


def _mid(low: float, high: float) -> float:
    return (low + high) / 2.0


def zone_key(ref: ZoneRef) -> str:
    return f"{ref.zone_type}|{ref.tf}|{ref.identity}"


def all_zone_refs(state: BridgeState) -> list[ZoneRef]:
    refs = []
    for z in state.order_blocks:
        refs.append(ZoneRef("OB", z.tf, 1 if z.direction == "BULLISH" else -1,
                             z.low, z.high, z.signature, tested=not z.virgin))
    for z in state.fvgs:
        refs.append(ZoneRef("FVG", z.tf, 1 if z.direction == "BULLISH" else -1,
                             z.low, z.high, z.name, tested=z.retested))
    return refs


def nearest_untested_opposite_zone(zones: list[ZoneRef], trade_direction: int, price: float) -> ZoneRef | None:
    """TP target: nearest untested zone on the opposite side of the trade
    direction, ahead of price - resistance for a long, support for a short."""
    opposite_dir = -trade_direction
    candidates = [
        z for z in zones
        if z.direction == opposite_dir and not z.tested
        and ((z.low > price) if trade_direction == 1 else (z.high < price))
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda z: abs(_mid(z.low, z.high) - price))


def second_nearest_zone_for_trailing(zones: list[ZoneRef], trade_direction: int, price: float) -> ZoneRef | None:
    """Trailing SL reference: second-nearest zone of the SAME direction as
    the trade, unified OB+FVG ranking, behind price (already passed through).
    Skips the nearest deliberately - gives room for a normal retest of the
    newest zone without a reversal signal before the move continues."""
    candidates = [
        z for z in zones
        if z.direction == trade_direction
        and ((z.high < price) if trade_direction == 1 else (z.low > price))
    ]
    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda z: abs(_mid(z.low, z.high) - price))
    return candidates[1]


def zone_broken(ref: ZoneRef, closed_candle: dict) -> bool:
    """A bullish zone breaks when a candle closes below its low (failed as
    support); a bearish zone breaks when a candle closes above its high
    (failed as resistance)."""
    if ref.direction == 1:
        return closed_candle["close"] < ref.low
    return closed_candle["close"] > ref.high
