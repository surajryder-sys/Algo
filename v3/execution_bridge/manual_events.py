"""Cross-component signal, read-only from Trend Manager's side: Execution
Bridge writes here the moment it detects a REAL close it didn't itself
initiate -- a manual cancellation/close, OR a genuine SL/TP hit (see
intervention.py). Broadened to cover SL/TP 2026-08-18: confirmed live
that Trend Manager's own state had NO other way to learn a real
position had been stopped out -- its only closure signal was the OB
itself getting mitigated on the chart, a completely separate thing --
so a real SL hit left the active_trade record showing FILLED
indefinitely with nothing real behind it. All three (manual/SL/TP) mean
the same thing from the source Manager's own point of view: this trade
is over in reality, close and permanently block it -- same treatment a
self-detected bias flip already gets ("a cancel is basically blocking
the trade" -- user, 2026-08-17).

Trend Manager reads this each cycle via
TradeTracker.should_react_to_manual_event. Same read-only cross-file
pattern already used throughout this system (Alert Manager reads
tv_scraper's zone files without ever writing to them) -- Execution
Bridge never writes into Trend Manager's own state file directly, and
Trend Manager never writes here.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def write_event(path: str, symbol: str) -> None:
    p = Path(path)
    try:
        raw = json.loads(p.read_text()) if p.exists() else {}
    except (json.JSONDecodeError, OSError):
        raw = {}
    raw[symbol] = time.time()
    p.write_text(json.dumps(raw))


def read_event_time(path: str, symbol: str) -> Optional[float]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    value = raw.get(symbol)
    return float(value) if value is not None else None
