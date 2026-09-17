"""Live MQL5-published ATR Trail Dual bridge reader. Ported verbatim from
v5_sentinel/bridge.py (2026-09-18) -- already fully symbol-parametrized
and stateless (no persisted state, so nothing needed changing for V6S's
multi-instrument design). Only the module docstring/log prefix below are
V6S's own.

The bridge is the PRIMARY (only) source for M3/M5/M1, via
bridge_bar_flip.py's own bar-close-gated state machine, which reads
read_lines()/read_close() below directly -- deliberately never the
bridge's own bundled "structure" field, only the raw line1/line2
trail_stop VALUES, run through the same geometric test flip_state.py
uses (see v5_sentinel's git history for the live false-positive that
shaped this rule originally).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

BRIDGE_FOLDER_NAME = "OBBridge"
MAX_AGE_SECONDS = 30.0  # indicator republishes every ~2s; well past that means stale/dead

# Confirmed live in V5-Sentinel (2026-09-08): "updated" alone isn't
# enough. A bridge file's "updated" timestamp can look fresh (touched
# within the last couple seconds) while its line1/line2 VALUES are
# actually several bars stale -- OnCalculate falling behind real bar
# closes keeps re-touching the file with the SAME old computed values.
# "updated" only proves the FILE was touched recently, not that the DATA
# inside kept pace with actual bar closes -- bar_time is what actually
# tracks that. Under normal operation `now - bar_time` cycles between 0
# and one bar's length; allowing up to 2 full bar-lengths gives a full
# bar's buffer for ordinary publish latency before treating it as
# genuinely stale.
BAR_STALENESS_MULTIPLIER = 2.0


def _bridge_root() -> Path:
    appdata = os.environ["APPDATA"]
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / BRIDGE_FOLDER_NAME


def _bar_time_stale(raw: dict, tf_minutes: int, symbol: str) -> bool:
    """True if this snapshot's own bar_time has fallen behind real bar
    closes by more than BAR_STALENESS_MULTIPLIER bar-lengths -- see
    BAR_STALENESS_MULTIPLIER's own comment. Silently passes (returns
    False) if bar_time isn't present at all -- an older bridge build
    that predates this field, or one that's never published a closed bar
    yet; MAX_AGE_SECONDS above is still the only check that applies in
    that case, unchanged from before."""
    bar_time = raw.get("bar_time")
    if bar_time is None:
        return False
    bar_age = time.time() - bar_time
    max_bar_age = tf_minutes * 60 * BAR_STALENESS_MULTIPLIER
    if bar_age > max_bar_age:
        print(f"[V6S-BRIDGE] {symbol} M{tf_minutes}: bar_time is {bar_age:.0f}s old "
              f"(max {max_bar_age:.0f}s) despite the file looking freshly updated -- "
              f"treating as stale, OnCalculate likely fell behind real bar closes")
        return True
    return False


def read_lines(symbol: str, tf_minutes: int) -> Optional[tuple[float, float]]:
    """(line1.trail_stop, line2.trail_stop) from the live bridge -- the
    raw trail VALUES, deliberately not the bundled "structure" field, see
    module docstring. None if the file is missing, unreadable, or stale
    (either the file itself hasn't been touched recently, OR it has but
    its own bar_time shows the DATA inside has fallen behind -- see
    _bar_time_stale)."""
    path = _bridge_root() / f"ATRSTATE_DUAL_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None

    age = time.time() - raw.get("updated", 0)
    if age > MAX_AGE_SECONDS:
        return None
    if _bar_time_stale(raw, tf_minutes, symbol):
        return None

    try:
        return float(raw["line1"]["trail_stop"]), float(raw["line2"]["trail_stop"])
    except (KeyError, TypeError, ValueError):
        return None


def read_close(symbol: str, tf_minutes: int) -> Optional[tuple[float, int]]:
    """(close, bar_time) of the bridge's own last CLOSED bar -- lets a
    caller that just needs "what did MQL5 close its own last bar at" read
    it straight off the bridge instead of a separate copy_rates call,
    which can independently disagree with what MQL5 itself computed off
    of. None if the file is missing/stale, or predates this field (older
    bridge builds won't have "close"/"bar_time" at all)."""
    path = _bridge_root() / f"ATRSTATE_DUAL_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None

    age = time.time() - raw.get("updated", 0)
    if age > MAX_AGE_SECONDS:
        return None
    if _bar_time_stale(raw, tf_minutes, symbol):
        return None

    try:
        return float(raw["close"]), int(raw["bar_time"])
    except (KeyError, TypeError, ValueError):
        return None
