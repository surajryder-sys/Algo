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

Carries the closed trade's own (exec_timeframe, exec_start_time)
identity alongside the timestamp, added 2026-08-25 after a real live
bug: a USTEC long's real SL hit generated this notification, but by the
time Trend Manager's own cycle got to processing it, Trend Manager had
ALREADY independently flip-closed that same long on its own (a newer
opposite parent OB won bias at essentially the same moment the SL hit)
and fired a brand new short. The notification carried only a symbol and
a timestamp -- no way to tell which trade it was actually about -- so
the close-event handler applied it to "whatever's currently active for
this symbol," which by then was the new short, not the long the
notification was really about. Confirmed live: that brand new short got
closed 3 seconds after opening, collateral damage from a stale
notification about an already-superseded trade. The identity fields let
should_react_to_close_event refuse to act unless the notification is
still about the CURRENTLY active trade, not just the same symbol.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def write_event(path: str, symbol: str, exec_timeframe: str, exec_start_time: int) -> None:
    p = Path(path)
    try:
        raw = json.loads(p.read_text()) if p.exists() else {}
    except (json.JSONDecodeError, OSError):
        raw = {}
    raw[symbol] = {"time": time.time(), "exec_timeframe": exec_timeframe, "exec_start_time": exec_start_time}
    p.write_text(json.dumps(raw))


def read_event(path: str, symbol: str) -> Optional[tuple]:
    """(event_time, exec_timeframe, exec_start_time) for the most recent
    real close Execution Bridge noticed for this symbol, or None if
    there's never been one. exec_timeframe/exec_start_time come back
    None for an old-format entry (a bare timestamp, written before this
    identity info existed, e.g. left over from before a restart) --
    callers treat that as "identity unknown, fall back to the old blind
    behavior" rather than refusing to ever react to it."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    value = raw.get(symbol)
    if value is None:
        return None
    if isinstance(value, dict):
        return float(value["time"]), value.get("exec_timeframe"), value.get("exec_start_time")
    return float(value), None, None  # pre-2026-08-25 format


def read_event_time(path: str, symbol: str) -> Optional[float]:
    """Backward-compatible timestamp-only accessor -- kept in case
    anything still wants just the "when," without the identity check."""
    event = read_event(path, symbol)
    return event[0] if event is not None else None
