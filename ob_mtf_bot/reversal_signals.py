"""Detects reversal signals at OB/FVG zones from the bridge, using fixed M1
confirmation candles - the same method used to trace a live price rejection
back to the exact zone it came from.

Three signal kinds, any one is sufficient:
  - rejection_close:  candle traded into the zone, closed back outside it
  - wick_rejection:    same, but the wick-into-zone portion dominates the
                        candle (>= WICK_REJECTION_MIN_RATIO times the body) -
                        a stronger version of a rejection close
  - engulfing:         a reversal-direction candle whose body engulfs the
                        prior candle's body, while touching the zone

A BEARISH zone (resistance) produces direction=-1 (short) signals: price must
approach from below and get rejected back down. A BULLISH zone (support)
produces direction=1 (long) signals: price must approach from above and get
rejected back up.

H4/H2/H1 zones are wide and their own candle takes a long time to close, so
for those a dominant wick alone (wick_rejection) is enough - no requirement
for the candle to close back outside the zone. M30/M15/M5 zones keep the
close-through requirement, since they're tight enough that it's achievable
and meaningful.

Read-only - identifies signals, does not place, modify, or close any orders.
"""
from __future__ import annotations

from dataclasses import dataclass

import MetaTrader5 as mt5

from ob_mtf_bot.bridge_reader import BridgeState

WICK_REJECTION_MIN_RATIO = 1.5
NO_CLOSE_REQUIRED_TFS = {"H4", "H2", "H1"}


@dataclass(frozen=True)
class ReversalSignal:
    zone_type: str      # "OB" or "FVG"
    tf: str
    identity: str        # OB signature or FVG name - stable zone key, not just its price range
    direction: int       # 1 = bullish signal (long), -1 = bearish signal (short)
    signal_kind: str     # "rejection_close", "wick_rejection", "engulfing"
    zone_low: float
    zone_high: float
    candle_time: int
    candle_open: float
    candle_high: float
    candle_low: float
    candle_close: float


def candle_dict(bar) -> dict:
    return {
        "time": int(bar["time"]), "open": float(bar["open"]), "high": float(bar["high"]),
        "low": float(bar["low"]), "close": float(bar["close"]),
    }


def check_bearish_zone(zone_low: float, zone_high: float, c: dict, tf: str) -> str | None:
    """Resistance zone. On H4/H2/H1, a dominant wick into the zone is enough
    on its own - no close-through required. On M30/M15/M5, price must also
    close back below the zone."""
    if c["high"] < zone_low:
        return None

    body = abs(c["close"] - c["open"])
    wick_into_zone = c["high"] - max(zone_low, c["open"], c["close"])

    if tf in NO_CLOSE_REQUIRED_TFS:
        if wick_into_zone > 0 and body > 0 and wick_into_zone >= WICK_REJECTION_MIN_RATIO * body:
            return "wick_rejection"
        return None

    if c["close"] >= zone_low:
        return None
    if wick_into_zone > 0 and body > 0 and wick_into_zone >= WICK_REJECTION_MIN_RATIO * body:
        return "wick_rejection"
    return "rejection_close"


def check_bullish_zone(zone_low: float, zone_high: float, c: dict, tf: str) -> str | None:
    """Support zone. Same H4/H2/H1 vs M30/M15/M5 split as check_bearish_zone."""
    if c["low"] > zone_high:
        return None

    body = abs(c["close"] - c["open"])
    wick_into_zone = min(zone_high, c["open"], c["close"]) - c["low"]

    if tf in NO_CLOSE_REQUIRED_TFS:
        if wick_into_zone > 0 and body > 0 and wick_into_zone >= WICK_REJECTION_MIN_RATIO * body:
            return "wick_rejection"
        return None

    if c["close"] <= zone_high:
        return None
    if wick_into_zone > 0 and body > 0 and wick_into_zone >= WICK_REJECTION_MIN_RATIO * body:
        return "wick_rejection"
    return "rejection_close"


def is_engulfing(prev: dict, cur: dict, direction: int) -> bool:
    prev_low = min(prev["open"], prev["close"])
    prev_high = max(prev["open"], prev["close"])
    if direction == 1:
        return cur["close"] > cur["open"] and cur["open"] <= prev_low and cur["close"] >= prev_high
    return cur["close"] < cur["open"] and cur["open"] >= prev_high and cur["close"] <= prev_low


def detect_signals(state: BridgeState, symbol: str, lookback_m1_bars: int = 30) -> list[ReversalSignal]:
    """Scans the last `lookback_m1_bars` closed M1 candles against every
    OB/FVG zone in the bridge state."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, lookback_m1_bars)
    if rates is None or len(rates) < 2:
        return []
    candles = [candle_dict(r) for r in rates]

    signals: list[ReversalSignal] = []

    def _scan_zone(zone_type: str, tf: str, identity: str, direction: int, low: float, high: float) -> None:
        for i, c in enumerate(candles):
            kind = check_bearish_zone(low, high, c, tf) if direction == -1 else check_bullish_zone(low, high, c, tf)
            if kind:
                signals.append(ReversalSignal(zone_type, tf, identity, direction, kind, low, high,
                                               c["time"], c["open"], c["high"], c["low"], c["close"]))
            if i > 0 and is_engulfing(candles[i - 1], c, direction):
                touches_zone = c["low"] <= high and c["high"] >= low
                if touches_zone:
                    signals.append(ReversalSignal(zone_type, tf, identity, direction, "engulfing", low, high,
                                                   c["time"], c["open"], c["high"], c["low"], c["close"]))

    for z in state.order_blocks:
        _scan_zone("OB", z.tf, z.signature, 1 if z.direction == "BULLISH" else -1, z.low, z.high)
    for z in state.fvgs:
        _scan_zone("FVG", z.tf, z.name, 1 if z.direction == "BULLISH" else -1, z.low, z.high)

    return signals
