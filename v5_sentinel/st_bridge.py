"""Supertrend bridge reader. Confirmed with the user 2026-09-12: the ATR
Dual/Major-Minor indicator now has Supertrend merged into it
(mql5/ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar.mq5, EnableSupertrend
switch, ST_-prefixed inputs) and publishes to its OWN bridge file --
SUPERTREND_{symbol}_{tf}.json, a SEPARATE file from ATRSTATE_DUAL_...,
same OBBridge Common Files folder (see PublishSTBridgeFile() in that
.mq5 for the exact writer). "either read from bridge if available,
already loaded on chart, embedded on single indicator" -- confirmed live
2026-09-12 that it's already publishing on every timeframe.

Bar-close-gated on the MQL5 side already: PublishSTBridgeFile() only
ever reads/writes closed_idx = rates_total - 2 (never the still-forming
bar), and computes its own `event_time` (the bar_time of the last
genuine trend FLIP, via FindSTEventTime()) the same way the ATR Dual
bridge's own line1/line2 event_time fields work. So this module does NO
flip-detection of its own -- just reads trend + event_time straight off
the file, same bridge-is-ground-truth philosophy bridge.py's own
ATR-dual reader already uses (no copy_rates recompute, no independent
tracking).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from v5_sentinel.bridge import MAX_AGE_SECONDS, _bar_time_stale, _bridge_root


@dataclass(frozen=True)
class SupertrendState:
    symbol: str
    timeframe_minutes: int
    trend: int          # 1 bullish, -1 bearish
    event_time: int       # bar_time of the last trend FLIP
    supertrend: float      # current ST line value (the trailing stop side currently active)
    close: float            # close of the last CLOSED bar
    bar_time: int             # that bar's own time


def read_supertrend(symbol: str, tf_minutes: int) -> Optional[SupertrendState]:
    """None if the file is missing, unreadable, stale (file not touched
    recently, or its own bar_time has fallen behind real bar closes --
    same two-tier staleness check bridge.py's own readers use), or
    predates this field set entirely (an indicator build with
    EnableSupertrend off, or ST_PublishToFile off)."""
    path = _bridge_root() / f"SUPERTREND_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None

    age = time.time() - raw.get("updated", 0)
    if age > MAX_AGE_SECONDS:
        return None
    if _bar_time_stale(raw, tf_minutes, symbol):
        return None

    try:
        return SupertrendState(
            symbol=symbol, timeframe_minutes=tf_minutes,
            trend=int(raw["trend"]), event_time=int(raw["event_time"]),
            supertrend=float(raw["supertrend"]), close=float(raw["close"]),
            bar_time=int(raw["bar_time"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
