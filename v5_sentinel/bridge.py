"""Live MQL5-published ATR Trail Dual bridge -- read ONLY as a tie-breaker
against our own copy_rates recompute. Confirmed with the user 2026-09-04,
after finding a real live disagreement between the two on M3 (a candle
the live chart showed clearing both lines cleanly, while the independent
recompute still read it as ambiguous): XAUUSD is volatile enough that
when the two disagree, the chart's own continuously-running values are
considered more reliable than an independent from-scratch recompute --
see flip_state.py's own docstring for why they can diverge at all (the
trail formula is a path-dependent ratchet with no decay, so a single
razor-thin disagreement anywhere in history persists indefinitely).

copy_rates recompute stays PRIMARY. This is only ever consulted to
resolve an actual disagreement, and is itself ignored whenever it has
nothing reliable to offer (file missing, stale, or the bridge's own two
lines disagree -- UNDECISIVE). No permanent dependency: if this bridge
ever stops publishing (indicator removed from the chart, terminal
restarted without reattaching it, etc.), everything silently falls back
to pure copy_rates, exactly as it behaved before this file existed.
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


def read_structure(symbol: str, tf_minutes: int) -> Optional[Confirmed]:
    """The live indicator's own STRONG/WEAK/UNDECISIVE reading for this
    timeframe, as a Confirmed value -- None if the file is missing,
    unreadable, stale, or itself UNDECISIVE (its two lines disagree --
    nothing reliable to defer to in that case either)."""
    path = _bridge_root() / f"ATRSTATE_DUAL_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None

    age = time.time() - raw.get("updated", 0)
    if age > MAX_AGE_SECONDS:
        return None

    structure = raw.get("structure")
    if structure == "STRONG":
        return Confirmed.BULL
    if structure == "WEAK":
        return Confirmed.BEAR
    return None  # UNDECISIVE, or a field this bridge version doesn't publish


def reconcile(fs: FlipStateResult, series: TrailSeries) -> FlipStateResult:
    """Returns fs unchanged if the bridge has nothing reliable to offer,
    or if it already agrees with our own reading (both confirmed AND not
    currently watching). Otherwise returns a new FlipStateResult trusting
    the bridge's direction -- confirmed set to it, watching cleared, far/
    near lines recomputed for the new direction, and last_event replaced
    with a SYNTHESIZED event dated to the current last closed bar (so
    event_just_happened() reads True and main.py's dedup treats this as a
    genuine fresh event to act on, exactly like a naturally-detected one).
    Labeled FLIP if the bridge direction differs from what we had
    confirmed before the override, TRAP_RESOLVED if it matches (i.e. we
    were watching toward a reversal that the bridge says didn't happen)."""
    bridge_dir = read_structure(fs.symbol, fs.timeframe_minutes)
    if bridge_dir is None:
        return fs

    disagreement = (fs.watching is not None) or (bridge_dir != fs.confirmed)
    if not disagreement:
        return fs

    print(f"[V5S-BRIDGE] {fs.symbol} M{fs.timeframe_minutes} disagreement -- "
          f"copyrates said {fs.confirmed.name}{' (watching ' + fs.watching.name + ')' if fs.watching else ''}, "
          f"live chart says {bridge_dir.name} -- trusting the chart")

    event_type = EventType.FLIP if bridge_dir != fs.confirmed else EventType.TRAP_RESOLVED
    synthesized_event = FlipEvent(bar_index=-1, bar_time=fs.last_time, event_type=event_type, confirmed=bridge_dir)

    far, near = far_near_line(bridge_dir.value, series.trail1[-1], series.trail2[-1])
    return dataclasses.replace(fs, confirmed=bridge_dir, watching=None, watching_since_time=None,
                               far_line=far, near_line=near, last_event=synthesized_event)
