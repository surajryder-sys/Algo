"""Live MQL5-published ATR Trail Dual bridge -- read ONLY as a tie-breaker
against our own copy_rates recompute. Confirmed with the user 2026-09-04,
after finding a real live disagreement between the two on M3 (a candle
the live chart showed clearing both lines cleanly, while the independent
recompute still read it as ambiguous): XAUUSD is volatile enough that
when the two disagree, the chart's own continuously-running trail VALUES
are considered more reliable than an independent from-scratch recompute
-- see flip_state.py's own docstring for why they can diverge at all (the
trail formula is a path-dependent ratchet with no decay, so a single
razor-thin disagreement anywhere in history persists indefinitely).

IMPORTANT, fixed 2026-09-04 same day after a live false-positive: this
does NOT use the bridge's own "structure" field (STRONG/WEAK/UNDECISIVE).
That field means "do line1's and line2's INDEPENDENTLY-tracked trend
flags currently agree" -- each line's flag only flips when price crosses
THAT line's OWN value, with no reference to the other line at all. It is
NOT the same question as "is price currently outside BOTH line values
together right now", which is what our confirmed/watching model actually
needs. Both flags can easily read the same direction from two unrelated
historical crossings while price is genuinely sitting BETWEEN the two
lines' current values -- confirmed live: a REFRESH fired off a bridge
"structure=WEAK" reading while the live chart itself showed no flip since
several hours earlier, and this module's own raw bars showed close sitting
BETWEEN the two current trail values at that exact bar. Reading the
bridge's raw trail_stop values instead and running them through the
SAME geometric test flip_state.py uses fixes this: now both systems
answer the literal same question, differing only in which trail VALUES
they feed it (which is the actual, legitimate thing we want the bridge to
correct for).

copy_rates recompute stays PRIMARY. This is only ever consulted to
resolve an actual disagreement, and is itself ignored whenever it has
nothing reliable to offer (file missing, stale, or the bridge's OWN raw
lines are themselves straddling the current close -- i.e. genuinely
ambiguous by the same test). No permanent dependency: if this bridge
ever stops publishing, everything silently falls back to pure
copy_rates, exactly as it behaved before this file existed.
"""
from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Optional

from v5_sentinel.flip_state import Confirmed, EventType, FlipEvent, FlipStateResult, far_near_line
from v5_sentinel.rates import TrailSeries

BRIDGE_FOLDER_NAME = "OBBridge"
MAX_AGE_SECONDS = 30.0  # indicator republishes every ~2s; well past that means stale/dead


def _bridge_root() -> Path:
    appdata = os.environ["APPDATA"]
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / BRIDGE_FOLDER_NAME


def read_lines(symbol: str, tf_minutes: int) -> Optional[tuple[float, float]]:
    """(line1.trail_stop, line2.trail_stop) from the live bridge -- the
    raw trail VALUES, deliberately not the bundled "structure" field, see
    module docstring. None if the file is missing, unreadable, or stale."""
    path = _bridge_root() / f"ATRSTATE_DUAL_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None

    age = time.time() - raw.get("updated", 0)
    if age > MAX_AGE_SECONDS:
        return None

    try:
        return float(raw["line1"]["trail_stop"]), float(raw["line2"]["trail_stop"])
    except (KeyError, TypeError, ValueError):
        return None


def _bridge_confirmed(close: float, bridge_lines: tuple[float, float]) -> Optional[Confirmed]:
    """Same geometric test flip_state.py's own compute() uses -- close
    strictly outside both bridge line values means confirmed that side;
    sitting between them (by the bridge's OWN values) means the bridge
    has nothing decisive to offer either, same as if it were missing."""
    lo, hi = min(bridge_lines), max(bridge_lines)
    if close > hi:
        return Confirmed.BULL
    if close < lo:
        return Confirmed.BEAR
    return None


def reconcile(fs: FlipStateResult, series: TrailSeries) -> FlipStateResult:
    """Returns fs unchanged if the bridge has nothing decisive to offer
    (missing/stale/its own lines straddle the current close too), or if
    it already agrees with our own reading (both confirmed AND not
    currently watching). Otherwise returns a new FlipStateResult trusting
    the bridge's line values -- confirmed set to what they geometrically
    imply, watching cleared, far/near lines recomputed from the BRIDGE's
    own trail values (not our recomputed ones), and last_event replaced
    with a SYNTHESIZED event dated to the current last closed bar (so
    event_just_happened() reads True and main.py's dedup treats this as a
    genuine fresh event to act on, exactly like a naturally-detected one).
    Labeled FLIP if the bridge direction differs from what we had
    confirmed before the override, TRAP_RESOLVED if it matches (i.e. we
    were watching toward a reversal that the bridge says didn't happen)."""
    bridge_lines = read_lines(fs.symbol, fs.timeframe_minutes)
    if bridge_lines is None:
        return fs

    bridge_dir = _bridge_confirmed(fs.last_close, bridge_lines)
    if bridge_dir is None:
        return fs  # bridge's own lines are themselves straddling price -- nothing to defer to

    disagreement = (fs.watching is not None) or (bridge_dir != fs.confirmed)
    if not disagreement:
        return fs

    print(f"[V5S-BRIDGE] {fs.symbol} M{fs.timeframe_minutes} disagreement -- "
          f"copyrates said {fs.confirmed.name}{' (watching ' + fs.watching.name + ')' if fs.watching else ''}, "
          f"live chart lines say {bridge_dir.name} (close={fs.last_close:.3f} vs bridge lines {bridge_lines}) "
          f"-- trusting the chart")

    event_type = EventType.FLIP if bridge_dir != fs.confirmed else EventType.TRAP_RESOLVED
    synthesized_event = FlipEvent(bar_index=-1, bar_time=fs.last_time, event_type=event_type, confirmed=bridge_dir)

    far, near = far_near_line(bridge_dir.value, bridge_lines[0], bridge_lines[1])
    return dataclasses.replace(fs, confirmed=bridge_dir, watching=None, watching_since_time=None,
                               far_line=far, near_line=near, last_event=synthesized_event)
