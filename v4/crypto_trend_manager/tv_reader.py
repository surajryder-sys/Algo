"""Reads tv_scraper's own already-computed output for BTCUSD/ETHUSD --
three separate files, each answering a different question this engine
needs:
  - trend.json  -- CONFIRMED (debounced) structure state + per-line trend,
                    each with its own event_time. This is what the M5
                    confirmation state machine and the M30/M15 STR bias
                    candidates key off -- see m5_confirm.py/parent_bias.py.
  - zones.json  -- live OB zones per timeframe/direction. Used for the ICT
                    bias candidates (most recent zone, either side) and
                    for the ICT-initiated SL (that zone's own edge).
  - live.json   -- raw current-poll trail_stop VALUES (not just
                    trend/event_time -- trend.json deliberately never
                    persists these, see atr_trend_tracker.py's own
                    _TrendState shape). Needed only for the STR-initiated
                    SL calc ("far ATR trailing stop with buffer"), which
                    needs the actual price level, not just direction.

Own dedicated small reader, not imported from v4/bridge or v3 -- same
"each bot gets its own copy, no cross-bot imports for bot-specific
readers" convention v4/bridge/tv_zones.py already documents, and the
exact filename mapping needed here (BTCUSD's default/unsuffixed files vs
ETHUSD's explicit tv_scraper_ethusd_* ones) doesn't match either existing
reader's assumed naming pattern anyway.

Liveness check (is_scraper_alive): deliberately NOT based on trend.json's
or zones.json's own file mtime -- both are commit-only saves (see
atr_trend_tracker.py's AtrTrendTracker._save(), only called when a value
actually CHANGES), so a long stretch of correctly-stable, nothing-to-
report data would look identical to a dead scraper by mtime alone. Uses
live.json's own `updated_at` instead -- that file is unconditionally
rewritten every single poll regardless of whether anything changed (see
its own module's docstring: "just 'what's on screen this poll'"), making
it the one genuine heartbeat signal tv_scraper produces.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

Direction = Literal["buy", "sell"]

_TREND_FILES = {"BTCUSD": "tv_scraper_trend.json", "ETHUSD": "tv_scraper_ethusd_trend.json"}
_ZONE_FILES = {"BTCUSD": "tv_scraper_zones.json", "ETHUSD": "tv_scraper_ethusd_zones.json"}
_LIVE_FILES = {"BTCUSD": "tv_scraper_live.json", "ETHUSD": "tv_scraper_ethusd_live.json"}

# Scraper polls every TV_SCRAPER_POLL_SECONDS (5s default) but cycles all 6
# panes sequentially within that, so any ONE pane's updated_at can lag the
# poll interval a bit even when perfectly healthy -- 30s gives real margin
# over that without masking an actually-dead scraper for long.
MAX_SCRAPER_AGE_SECONDS = 30.0


def is_scraper_alive(symbol: str, now: Optional[float] = None) -> bool:
    """False if tv_scraper's own live snapshot for this symbol is missing,
    unreadable, or hasn't been touched by ANY pane recently -- the one
    genuine "is the whole scraper loop still executing" signal available
    (see this module's own docstring for why trend.json/zones.json's
    mtime can't be used for this)."""
    now = time.time() if now is None else now
    raw = _read_json(_LIVE_FILES[symbol])
    if not raw:
        return False
    newest = max((entry.get("updated_at", 0.0) for entry in raw.values()), default=0.0)
    return (now - newest) <= MAX_SCRAPER_AGE_SECONDS


def _read_json(path: str) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


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
    raw = _read_json(_TREND_FILES[symbol])
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


@dataclass
class ObZone:
    direction: Direction
    start_time: int
    top: float
    btm: float


def read_latest_ob(symbol: str, tf_minutes: int) -> Optional[ObZone]:
    """The single most recently FORMED (by start_time -- the zone's own
    real bar timestamp, not the scraper's detected_time artifact) zone on
    this timeframe, either direction. Re-derived fresh from the live zones
    file every call -- once a zone is mitigated, ZoneStore deletes it (see
    v3/tv_scraper/zone_history_log.py's own docstring), so an invalidated
    ICT candidate simply stops being returned here on its own, with no
    extra bookkeeping needed by callers."""
    raw = _read_json(_ZONE_FILES[symbol])
    if raw is None:
        return None

    best: Optional[ObZone] = None
    for direction, bull_bear in (("buy", "bull"), ("sell", "bear")):
        zones = raw.get(f"{symbol}|{tf_minutes}|{bull_bear}", {})
        for z in zones.values():
            if best is None or z["start_time"] > best.start_time:
                best = ObZone(direction=direction, start_time=z["start_time"], top=z["top"], btm=z["btm"])
    return best


def read_trail_stops(symbol: str, tf_minutes: int) -> Optional[tuple[Optional[float], Optional[float]]]:
    """(line1_trail_stop, line2_trail_stop) -- raw current values from the
    live snapshot, for the STR-initiated SL calc only. Either can be None
    if that line's plot hasn't rendered this particular poll."""
    raw = _read_json(_LIVE_FILES[symbol])
    if raw is None:
        return None
    entry = raw.get(f"{symbol}|{tf_minutes}")
    if entry is None:
        return None
    atr = entry.get("atr") or {}
    l1 = atr.get("line1", {}).get("trail_stop")
    l2 = atr.get("line2", {}).get("trail_stop")
    return l1, l2
