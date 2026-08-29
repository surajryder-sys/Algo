"""Reads tv_scraper's own already-computed M1 combined ATR structure --
the same {"combined": {"SYMBOL|1": {"state", "event_time"}}} section
v3/tv_scraper/atr_trend_tracker.py persists, and v4/bridge/tv_atr.py reads
for XAUUSD. A small dedicated copy here rather than importing v4's reader
-- this package isn't part of V4 and shouldn't depend on it (same
"each bot gets its own small reader, no cross-bot imports" convention
v4/bridge/tv_zones.py's own docstring documents for the equivalent
zones case).

No cursor/state needed -- this file is tiny and fully overwritten on
every scraper save, so a fresh read each poll is cheap and always current.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# BTCUSD uses the scraper's default (unsuffixed) file; ETHUSD's launch
# uses TV_SCRAPER_TREND_STATE_FILE="tv_scraper_ethusd_trend.json" -- see
# [[project_tv_scraper_multi_symbol_setup]]. Not env-driven here since
# this package only ever races these two specific symbols.
_TREND_FILES = {
    "BTCUSD": "tv_scraper_trend.json",
    "ETHUSD": "tv_scraper_ethusd_trend.json",
}


def read_scraper_structure(symbol: str) -> Optional[tuple[str, int]]:
    """(structure, structure_event_time) for this symbol's M1 combined
    reading, or None if the file's missing, mid-write, or has no M1 entry
    yet (all treated the same as any other transient bridge-read gap)."""
    path = _TREND_FILES.get(symbol)
    if path is None:
        return None
    try:
        raw = json.loads(Path(path).read_text())
        entry = raw["combined"][f"{symbol}|1"]
        return entry["state"], int(entry["event_time"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
