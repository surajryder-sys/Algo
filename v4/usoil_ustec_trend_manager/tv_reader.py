"""Reads tv_scraper's own already-computed output for USOIL/USTEC -- two
files, each answering a different question this engine needs (same shape
as crypto_trend_manager's own tv_reader.py):
  - trend.json  -- CONFIRMED (debounced) structure state + per-line trend,
                    each with its own event_time. This is what the M5
                    confirmation state machine and the M30/M15 bias
                    candidates key off -- see m5_confirm.py/parent_bias.py.
  - live.json   -- raw current-poll trail_stop VALUES, needed for the SL
                    calc ("far ATR trailing stop with buffer").

UNLIKE crypto_trend_manager, USOIL and USTEC share ONE tv_scraper window/
process (explicit user choice, 2026-08-30/31 -- "Shared, one window for
both") -- both symbols' data lands in the SAME two files (each pane
self-detects its own symbol/timeframe, so one file naturally holds both
symbols' keys side by side). So there is no per-symbol filename dict here,
just one constant path each.

OB zones are not read at all -- entries here are structure-only from the
start, matching crypto_trend_manager's own post-removal design (see that
package's parent_bias.py for why ICT was dropped entirely).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_TREND_FILE = "tv_scraper_usoil_ustec_trend.json"
_LIVE_FILE = "tv_scraper_usoil_ustec_live.json"

# Same margin reasoning as crypto_trend_manager's own MAX_SCRAPER_AGE_SECONDS.
MAX_SCRAPER_AGE_SECONDS = 30.0


def _read_json(path: str) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def is_scraper_alive(now: Optional[float] = None) -> bool:
    """One shared scraper serves both symbols, so this isn't per-symbol
    like crypto_trend_manager's own version -- either both symbols are
    live or neither is, since they come from the same process/window."""
    now = time.time() if now is None else now
    raw = _read_json(_LIVE_FILE)
    if not raw:
        return False
    newest = max((entry.get("updated_at", 0.0) for entry in raw.values()), default=0.0)
    return (now - newest) <= MAX_SCRAPER_AGE_SECONDS


@dataclass
class LineReading:
    trend: Optional[int]
    event_time: Optional[int]


@dataclass
class StructureReading:
    state: str  # "STRONG" | "WEAK" | "UNDECISIVE"
    event_time: Optional[int]
    line1: LineReading
    line2: LineReading


def read_structure(symbol: str, tf_minutes: int) -> Optional[StructureReading]:
    raw = _read_json(_TREND_FILE)
    if raw is None:
        return None
    key = f"{symbol}|{tf_minutes}"
    combined = raw.get("combined", {}).get(key)
    if combined is None:
        return None
    lines = raw.get("lines", {})

    def _line(n: int) -> LineReading:
        l = lines.get(f"{key}|line{n}")
        if l is None:
            return LineReading(None, None)
        return LineReading(trend=l.get("trend"), event_time=l.get("event_time"))

    return StructureReading(state=combined["state"], event_time=combined.get("event_time"),
                             line1=_line(1), line2=_line(2))


def read_trail_stops(symbol: str, tf_minutes: int) -> Optional[tuple[Optional[float], Optional[float]]]:
    """(line1_trail_stop, line2_trail_stop) -- raw current values from the
    live snapshot, for the SL calc only. Either can be None if that
    line's plot hasn't rendered this particular poll."""
    raw = _read_json(_LIVE_FILE)
    if raw is None:
        return None
    entry = raw.get(f"{symbol}|{tf_minutes}")
    if entry is None:
        return None
    atr = entry.get("atr") or {}
    l1 = atr.get("line1", {}).get("trail_stop")
    l2 = atr.get("line2", {}).get("trail_stop")
    return l1, l2
