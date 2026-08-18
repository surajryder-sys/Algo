"""Cross-component signal, read-only from Trend Manager's side: Execution
Bridge writes here the moment it detects a REAL manual cancellation or
close in MT5 (see intervention.py); Trend Manager reads it each cycle
to trigger the same permanent watermark block a self-detected bias flip
already gets ("a cancel is basically blocking the trade" -- user,
2026-08-17). Same read-only cross-file pattern already used throughout
this system (Alert Manager reads tv_scraper's zone files without ever
writing to them) -- Execution Bridge never writes into Trend Manager's
own state file directly, and Trend Manager never writes here.
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
