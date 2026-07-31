"""Reads ATR Trail zone snapshots published by ATR_Trail_Bridge_v1.00.mq5
through the same MT5 Common Files JSON bridge the OB indicator uses.

The indicator runs on a single chart/timeframe and writes
<CommonFiles>/OBBridge/ATRSTATE_<symbol>_<tf_minutes>.json on every scan.
This module only reads that file; it does not talk to the terminal.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from ob_bridge.reader import bridge_root


@dataclass
class ATRSnapshot:
    symbol: str
    timeframe_minutes: int
    updated: int
    trail_stop: float
    trend: int         # 1 = Strong (close above trail), -1 = Weak (close below trail)
    event_time: int     # bar time of the most recent Strong<->Weak flip

    def age_seconds(self) -> float:
        return time.time() - self.updated

    def is_stale(self, max_age_seconds: float = 30.0) -> bool:
        return self.updated == 0 or self.age_seconds() > max_age_seconds


def _parse(raw: dict) -> ATRSnapshot:
    return ATRSnapshot(
        symbol=raw["symbol"],
        timeframe_minutes=raw["timeframe_minutes"],
        updated=raw["updated"],
        trail_stop=raw["trail_stop"],
        trend=raw["trend"],
        event_time=raw["event_time"],
    )


def read_atr(symbol: str, tf_minutes: int) -> Optional[ATRSnapshot]:
    """Returns None if the file is missing, was read mid-write, or is briefly
    locked by Windows during the indicator's write-then-rename (OSError
    covers PermissionError here) -- all transient, safe to just retry next
    poll rather than aborting the caller's whole cycle."""
    path = bridge_root() / f"ATRSTATE_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    return _parse(raw)
