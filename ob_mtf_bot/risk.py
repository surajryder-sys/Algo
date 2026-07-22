"""Stop-loss floor and geometry checks, mirroring ApplyMinimumSL / SLGeometryValid."""
from __future__ import annotations

import MetaTrader5 as mt5

from ob_mtf_bot.config import Config


def apply_minimum_sl(cfg: Config, direction: int, entry: float, logical_sl: float, digits: int) -> float:
    """Preserve the logical (order-block-based) SL unless it's closer than the
    configured minimum distance, in which case widen it to exactly the minimum."""
    if direction == 1:
        if entry - logical_sl < cfg.min_sl_distance:
            return round(entry - cfg.min_sl_distance, digits)
        return round(logical_sl, digits)

    if logical_sl - entry < cfg.min_sl_distance:
        return round(entry + cfg.min_sl_distance, digits)
    return round(logical_sl, digits)


def sl_geometry_valid(symbol: str, direction: int, entry: float, sl: float) -> bool:
    if direction == 1 and sl >= entry:
        return False
    if direction == -1 and sl <= entry:
        return False

    info = mt5.symbol_info(symbol)
    broker_min = (info.trade_stops_level or 0) * info.point
    if broker_min <= 0:
        return True
    return (entry - sl >= broker_min) if direction == 1 else (sl - entry >= broker_min)
