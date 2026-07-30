"""Reads OB zone snapshots published by OB_StatePublisher_Indicator through
the MT5 Common Files JSON bridge.

The indicator runs once, attached to a single chart, and writes
<CommonFiles>/OBBridge/OBSTATE_<symbol>_<tf_minutes>.json per configured
timeframe on every scan. This module only reads those files; it does not
talk to the terminal.

Independent copy for the BTCUSD bot -- the MT5 Common Files folder is
shared across every terminal install for this Windows user (that's how MT5
implements it), so this reads from the same physical bridge folder as the
XAUUSD and ETHUSD bots' readers. That's fine: OBSTATE files are already
namespaced by symbol, so a BTCUSD-attached indicator instance (running in
the separate MT5 terminal at BTC_SMC_MT5_TERMINAL_PATH) never collides with
the others.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TIMEFRAMES: dict[str, int] = {
    "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H2": 120, "H4": 240,
}

BRIDGE_FOLDER_NAME = "OBBridge"


def bridge_root() -> Path:
    appdata = os.environ["APPDATA"]
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / BRIDGE_FOLDER_NAME


@dataclass
class Zone:
    high: float
    low: float
    virgin: bool
    start_time: int
    detected_time: int
    detected_price: float


@dataclass
class OBSnapshot:
    symbol: str
    timeframe_minutes: int
    updated: int
    bias: int
    latest_high: float
    latest_low: float
    latest_virgin: bool
    latest_time: int
    detected_time: int
    detected_price: float
    visit_time: int
    validation_time: int
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


def _parse(raw: dict) -> OBSnapshot:
    latest = raw["latest"]
    return OBSnapshot(
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
        visit_time=latest["visit_time"],
        validation_time=latest["validation_time"],
        bull=[_parse_zone(z) for z in raw["bull"]],
        bear=[_parse_zone(z) for z in raw["bear"]],
    )


def read_zone(symbol: str, tf_minutes: int) -> Optional[OBSnapshot]:
    """Returns None if the file is missing, was read mid-write, or is briefly
    locked by Windows during the indicator's write-then-rename (OSError
    covers PermissionError here) -- all transient, safe to just retry next
    poll rather than aborting the caller's whole cycle."""
    path = bridge_root() / f"OBSTATE_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    return _parse(raw)


def read_all(symbol: str) -> dict[str, Optional[OBSnapshot]]:
    """One snapshot per configured timeframe label (M5/M15/M30/H1/H2/H4);
    None where the indicator hasn't published for that timeframe yet."""
    return {label: read_zone(symbol, minutes) for label, minutes in TIMEFRAMES.items()}
