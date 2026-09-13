"""Reader for MT5's OB_Zone_Bridge_Lite output -- Trend Manager's ICT
component's own MT5-side OB zone source (see trend_manager_ict.py's own
docstring for the full design).

Confirmed live 2026-09-14, investigating the user's own recollection that
"ob is already attached to chart which publishes almost everything ATR
Trail Dual Values, Supertrend values, ob's with top bottom also major,
minor, etc": that's actually TWO separate indicators, not one --
ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar.mq5 (bridge.py/
st_bridge.py's own source) publishes ONLY the ATR-dual/Supertrend/
HammerStar bridge files, no OB data at all. mql5/OB_Zone_Bridge_Lite.mq5
is the SEPARATE, older (V4-era) indicator that actually publishes OB zone
data, to its own OBSTATE_LITE_{symbol}_{minutes}.json file in the same
OBBridge Common Files folder -- confirmed live and fresh on M1/M3/M5/M15
XAUUSD charts on 2026-09-14.

That indicator does no OB detection of its own -- it scans for
rectangle objects (name containing "pineBox") already drawn on the chart
by a separate LuxAlgo-style OB-detector script, then publishes
high/low/virgin/start_time/detected_time/detected_price per zone. Two
fields worth knowing before using this:

  - `start_time` is the zone's own real formation bar (from the drawn
    rectangle itself) -- always populated, baseline or not. This is what
    "when did this OB form" should be read from.
  - `detected_time`/`detected_price` are a DIFFERENT thing: when this
    INDICATOR's own scan first noticed the rectangle (bookkeeping for its
    own virgin/retest reconstruction, not the OB's real formation time).
    Any zone that already existed as a chart rectangle at the moment this
    indicator was first attached is a "baseline" zone, frozen at
    detected_time=0/detected_price=0.0 forever -- confirmed live, every
    zone in XAUUSD M3's own file reads that way right now (M1/M5/M15 do
    have some non-baseline entries, i.e. fresh zones already formed since
    those instances were attached). Never use these two fields as an OB's
    own formation timestamp -- use start_time for that.

Same missing/mid-write/stale contract as every other bridge reader in
this project (bridge.py/st_bridge.py) -- None on any failure, no fallback,
no guess.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v5_sentinel.bridge import _bridge_root

DEFAULT_MAX_AGE_SECONDS = 30.0  # OB_Zone_Bridge_Lite.mq5's own ScanEverySeconds default is 5s


@dataclass(frozen=True)
class LiteZone:
    high: float
    low: float
    virgin: bool
    start_time: int          # the zone's own real formation bar -- see module docstring
    detected_time: int        # this INDICATOR's own bookkeeping -- 0 for a pre-attachment baseline zone
    detected_price: float


@dataclass(frozen=True)
class LiteSnapshot:
    symbol: str
    timeframe_minutes: int
    updated: int
    bias: int                  # direction of the single most-recently-formed zone (either side), 0 if none yet
    latest_high: float
    latest_low: float
    latest_virgin: bool
    latest_time: int
    bull: list           # list[LiteZone], newest first
    bear: list            # list[LiteZone], newest first

    def age_seconds(self) -> float:
        return time.time() - self.updated


def _parse_zone(raw: dict) -> LiteZone:
    return LiteZone(
        high=float(raw["high"]), low=float(raw["low"]), virgin=bool(raw["virgin"]),
        start_time=int(raw["start_time"]), detected_time=int(raw["detected_time"]),
        detected_price=float(raw["detected_price"]),
    )


def read_lite(symbol: str, tf_minutes: int, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> Optional[LiteSnapshot]:
    """None if the file is missing, unreadable, mid-write, or stale (this
    indicator hasn't published in a while -- e.g. its chart was closed)."""
    path = _bridge_root() / f"OBSTATE_LITE_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None

    updated = int(raw.get("updated", 0))
    if time.time() - updated > max_age_seconds:
        return None

    try:
        latest = raw["latest"]
        return LiteSnapshot(
            symbol=raw["symbol"], timeframe_minutes=int(raw["timeframe_minutes"]), updated=updated,
            bias=int(raw["bias"]),
            latest_high=float(latest["high"]), latest_low=float(latest["low"]),
            latest_virgin=bool(latest["virgin"]), latest_time=int(latest["time"]),
            bull=[_parse_zone(z) for z in raw["bull"]],
            bear=[_parse_zone(z) for z in raw["bear"]],
        )
    except (KeyError, TypeError, ValueError):
        return None
