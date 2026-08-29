"""Reads XAUUSD M1's dual-ATR combined structure straight off
v3/tv_scraper's own trend-state file (tv_scraper_xauusd_trend.json at the
repo root) -- the TradingView-side counterpart to v4.bridge.reader's
MT5-native read_atr_dual, raced against it per explicit instruction
2026-08-28: "wire it with TradingView's ATR flip as well, whichever gives
first confirmation, we go based on that -- TradingView is sometimes more
accurate." Reads the raw JSON directly rather than importing
v3.tv_scraper.atr_trend_tracker -- same v3-isolation reasoning as
tv_zones.py.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]


def trend_state_path(symbol: str = "XAUUSD") -> Path:
    env_override = os.getenv("V4_TV_TREND_STATE_FILE")
    if env_override:
        return Path(env_override)
    return _REPO_ROOT / f"tv_scraper_{symbol.lower()}_trend.json"


@dataclass
class TVStructure:
    state: str          # "STRONG" | "WEAK" | "UNDECISIVE"
    event_time: int      # bar time this combined label last changed (TradingView's own clock)


def read_tv_structure(symbol: str, tf_minutes: int) -> Optional[TVStructure]:
    """Returns None if the file is missing/mid-write/unreadable, or if
    this timeframe hasn't been committed yet -- same transient-failure
    contract as v4.bridge.reader's MT5-side reads."""
    path = trend_state_path(symbol)
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    entry = raw.get("combined", {}).get(f"{symbol}|{tf_minutes}")
    if entry is None:
        return None
    try:
        return TVStructure(state=entry["state"], event_time=entry["event_time"])
    except (KeyError, TypeError):
        return None
