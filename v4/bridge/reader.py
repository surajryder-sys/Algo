"""Reads V4's two MT5-native bridge files -- the dual-ATR-trail structure
bridge and the lightweight OB zone bridge -- both published by indicators
attached individually to V4's three execution-engine charts (M5/M3/M1),
one instance per chart (see mql5/SurajBot_ATRTrail_FINAL_LIVEFIXED_
REALTIME_DUAL.mq5 and mql5/OB_Zone_Bridge_Lite.mq5's own docstrings).

Deliberately separate from ob_bridge/atr_bridge (which read algo_v2's own
bridge files): different JSON schema on both sides -- dual trail lines
plus a combined structure reading here instead of one trail line, and a
trimmed OB schema here (no visit_time/validation_time -- deliberately left
out of the MT5 side per explicit request, kept lean) instead of the full
OBSnapshot shape those modules parse. Only `bridge_root()` (same Common
Files\\OBBridge folder both bridges share) is reused from ob_bridge.reader
rather than duplicated.

This module only reads files; it does not talk to the MT5 terminal.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from ob_bridge.reader import bridge_root

# V4's execution engine only ever needs these three -- see this package's
# own docstring / CLAUDE.md for why (M5+M3 = structure, M3+M1 = execution).
EXECUTION_TIMEFRAMES: dict[str, int] = {"M5": 5, "M3": 3, "M1": 1}


@dataclass
class ATRLine:
    trail_stop: float
    trend: int   # 1 = bullish (close above trail), -1 = bearish (close below trail)
    event_time: int  # bar time of this line's own most recent flip


@dataclass
class ATRDualSnapshot:
    symbol: str
    timeframe_minutes: int
    updated: int
    line1: ATRLine   # fast (default ATR period 2)
    line2: ATRLine   # slow (default ATR period 300)
    structure: str   # "STRONG" | "WEAK" | "UNDECISIVE" -- both lines agreeing bull/bear, else UNDECISIVE
    structure_event_time: int  # bar time the combined `structure` label itself last changed

    def age_seconds(self) -> float:
        return time.time() - self.updated

    def is_stale(self, max_age_seconds: float = 30.0) -> bool:
        return self.updated == 0 or self.age_seconds() > max_age_seconds


def _parse_atr_line(raw: dict) -> ATRLine:
    return ATRLine(trail_stop=raw["trail_stop"], trend=raw["trend"], event_time=raw["event_time"])


def _parse_atr_dual(raw: dict) -> ATRDualSnapshot:
    return ATRDualSnapshot(
        symbol=raw["symbol"],
        timeframe_minutes=raw["timeframe_minutes"],
        updated=raw["updated"],
        line1=_parse_atr_line(raw["line1"]),
        line2=_parse_atr_line(raw["line2"]),
        structure=raw["structure"],
        structure_event_time=raw["structure_event_time"],
    )


def read_atr_dual(symbol: str, tf_minutes: int) -> Optional[ATRDualSnapshot]:
    """Returns None if the file is missing, was read mid-write, or is
    briefly locked by Windows during the indicator's write-then-rename --
    all transient, safe to just retry next poll (same contract as
    atr_bridge.reader.read_atr / ob_bridge.reader.read_zone)."""
    path = bridge_root() / f"ATRSTATE_DUAL_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    return _parse_atr_dual(raw)


@dataclass
class Zone:
    high: float
    low: float
    virgin: bool
    start_time: int
    detected_time: int
    detected_price: float


@dataclass
class OBLiteSnapshot:
    symbol: str
    timeframe_minutes: int
    updated: int
    bias: int   # direction of the single most-recently-formed zone (either side), 0 if none yet
    latest_high: float
    latest_low: float
    latest_virgin: bool
    latest_time: int
    detected_time: int
    detected_price: float
    bull: list[Zone]   # newest first, up to ZoneHistoryDepth entries
    bear: list[Zone]   # newest first, up to ZoneHistoryDepth entries

    def age_seconds(self) -> float:
        return time.time() - self.updated

    def is_stale(self, max_age_seconds: float = 30.0) -> bool:
        return self.updated == 0 or self.age_seconds() > max_age_seconds

    def latest_untested(self, direction: str) -> Optional[Zone]:
        """First virgin zone in bull/bear history (newest first), or None."""
        history = self.bull if direction == "bull" else self.bear
        for zone in history:
            if zone.virgin:
                return zone
        return None


def _parse_zone(raw: dict) -> Zone:
    return Zone(
        high=raw["high"],
        low=raw["low"],
        virgin=raw["virgin"],
        start_time=raw["start_time"],
        detected_time=raw["detected_time"],
        detected_price=raw["detected_price"],
    )


def _parse_ob_lite(raw: dict) -> OBLiteSnapshot:
    latest = raw["latest"]
    return OBLiteSnapshot(
        symbol=raw["symbol"],
        timeframe_minutes=raw["timeframe_minutes"],
        updated=raw["updated"],
        bias=raw["bias"],
        latest_high=latest["high"],
        latest_low=latest["low"],
        latest_virgin=latest["virgin"],
        latest_time=latest["time"],
        detected_time=latest["detected_time"],
        detected_price=latest["detected_price"],
        bull=[_parse_zone(z) for z in raw["bull"]],
        bear=[_parse_zone(z) for z in raw["bear"]],
    )


def read_zone_lite(symbol: str, tf_minutes: int) -> Optional[OBLiteSnapshot]:
    """Same transient-failure contract as read_atr_dual above."""
    path = bridge_root() / f"OBSTATE_LITE_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    return _parse_ob_lite(raw)


@dataclass
class TFSnapshot:
    """Both bridges' readings for one timeframe, bundled -- what V4's
    Trend Manager actually consumes per poll."""
    label: str            # "M5" | "M3" | "M1"
    minutes: int
    atr: Optional[ATRDualSnapshot]
    ob: Optional[OBLiteSnapshot]


def read_all(symbol: str) -> dict[str, TFSnapshot]:
    """One bundled snapshot per V4 execution timeframe (M5/M3/M1). Either
    side can be None if that bridge hasn't published yet / is mid-write --
    callers must handle that the same way atr_bridge/ob_bridge callers
    already do, not assume both are always present."""
    out: dict[str, TFSnapshot] = {}
    for label, minutes in EXECUTION_TIMEFRAMES.items():
        out[label] = TFSnapshot(
            label=label,
            minutes=minutes,
            atr=read_atr_dual(symbol, minutes),
            ob=read_zone_lite(symbol, minutes),
        )
    return out
