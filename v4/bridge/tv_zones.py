"""Reads XAUUSD's full 8-timeframe OB zone picture straight off
`v3/tv_scraper`'s own zone-state file (`tv_scraper_xauusd_zones.json` at
the repo root) -- the same file explored manually earlier this session.

Deliberately reads the raw JSON directly rather than importing
`v3.tradingview_bot.zone_store.ZoneStore` -- V4 does not import from v3's
folder (or vice versa), same isolation every other bot in this repo
keeps (see CLAUDE.md). tv_scraper is still the right SOURCE for this data
(all 8 TFs, not just V4's own M5/M3/M1 MT5 bridges) -- only the Python
that reads it is kept separate.

Every zone here is a candidate "reversal zone" for V4's Trend Manager
(and later Reversal Manager): a zone's own DIRECTION alone sets which
side it buffers --
  bull (demand) zone -> no_short  (a short would be trading against it)
  bear (supply) zone -> no_long   (a long would be trading against it)
Buffer activation rule (explicit, 2026-08-28): measured from each zone's
own BASELINE (center/average price, not its high/low edges) to the
qualifying setup's price -- a long is blocked if any bear zone's baseline
sits within 4 points (XAUUSD) of the setup price; a short is blocked if
any bull zone's baseline sits within 4 points. This module only exposes
`baseline` per zone; the actual 4-point gate check belongs to whatever
trade-initiation logic reads this data, not here.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TIMEFRAMES: dict[str, int] = {
    "H4": 240, "H2": 120, "H1": 60, "M30": 30,
    "M15": 15, "M5": 5, "M3": 3, "M1": 1,
}

# Repo root -- this file lives at <root>/v4/bridge/tv_zones.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def zone_state_path(symbol: str = "XAUUSD") -> Path:
    env_override = os.getenv("V4_TV_ZONE_STATE_FILE")
    if env_override:
        return Path(env_override)
    return _REPO_ROOT / f"tv_scraper_{symbol.lower()}_zones.json"


@dataclass
class Zone:
    high: float
    low: float
    baseline: float   # zone's own center/average price ("avg" in the scraper's
                       # raw JSON) -- what the no_long/no_short buffer distance
                       # check below is measured from, not the high/low edges.
    virgin: bool
    start_time: int
    retested_at: Optional[int]
    buffer_direction: str  # "no_short" | "no_long"


@dataclass
class TFZones:
    label: str      # "H4".."M1"
    minutes: int
    bull: list[Zone]   # active (unmitigated) only, newest first
    bear: list[Zone]   # active (unmitigated) only, newest first


def _parse_zone(raw: dict, buffer_direction: str) -> Zone:
    # Falls back to computing the midpoint if "avg" is ever absent (older
    # zone_state_file entries predating that field) rather than raising --
    # matches this file's own "safe to retry / degrade" contract elsewhere.
    baseline = raw.get("avg")
    if baseline is None:
        baseline = (raw["top"] + raw["btm"]) / 2.0
    return Zone(
        high=raw["top"],
        low=raw["btm"],
        baseline=baseline,
        virgin=raw["virgin"],
        start_time=raw["start_time"],
        retested_at=raw.get("retested_at"),
        buffer_direction=buffer_direction,
    )


def _active_zones(all_zones: dict, symbol: str, tf: str, direction: str, buffer_direction: str) -> list[Zone]:
    """all_zones is the raw decoded JSON: {"SYMBOL|tf|direction": {start_time_str: zone_dict}}."""
    by_start_time = all_zones.get(f"{symbol}|{tf}|{direction}", {})
    zones = [
        _parse_zone(z, buffer_direction)
        for z in by_start_time.values()
        if z.get("mitigated_time") is None
    ]
    zones.sort(key=lambda z: -z.start_time)
    return zones


def read_all_zones(symbol: str = "XAUUSD") -> Optional[dict[str, TFZones]]:
    """One TFZones per H4..M1, or None if the scraper's zone file is
    missing / mid-write / unreadable this poll -- transient, safe to
    retry (same contract as v4.bridge.reader's MT5-side reads)."""
    path = zone_state_path(symbol)
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    out: dict[str, TFZones] = {}
    for label, minutes in TIMEFRAMES.items():
        tf = str(minutes)
        out[label] = TFZones(
            label=label,
            minutes=minutes,
            bull=_active_zones(raw, symbol, tf, "bull", "no_short"),
            bear=_active_zones(raw, symbol, tf, "bear", "no_long"),
        )
    return out


def file_age_seconds(symbol: str = "XAUUSD") -> Optional[float]:
    """How long since the scraper last wrote this file -- None if it
    doesn't exist. Useful for a staleness check the way ATRDualSnapshot/
    OBLiteSnapshot.is_stale() serve the MT5 bridges, since this file has
    no internal `updated` timestamp of its own to check instead."""
    path = zone_state_path(symbol)
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None
