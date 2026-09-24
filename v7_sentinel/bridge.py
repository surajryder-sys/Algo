"""Live MQL5-published ATR Trail Dual bridge reader. Ported verbatim from
v5_sentinel/bridge.py (2026-09-18) -- already fully symbol-parametrized
and stateless (no persisted state, so nothing needed changing for V7S's
multi-instrument design). Only the module docstring/log prefix below are
V7S's own.

read_lines()/read_close() below are how V7S reads the live ATR-dual
bridge -- deliberately never the bridge's own bundled "structure" field,
only the raw line1/line2 trail_stop VALUES, run through the same
geometric test flip_state.py uses (see v5_sentinel's git history for the
live false-positive that shaped this rule originally). RM's entry logic
no longer reads this bridge at all (native copy_rates instead); the one
remaining consumer is bridge_flip.m3_far_line(), for post-breakeven SL
trailing.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
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
# tracks that. bar_time is the OPEN time of the last CLOSED bar, so under
# normal operation `now - bar_time` cycles between ONE and TWO bar
# lengths -- and hits exactly TWO the instant a bar closes, until the
# indicator republishes with the newly closed bar. Allowing only 2 bar
# lengths therefore made EVERY reader (ATR lines, Supertrend, CISD) return
# "stale" for ~0.45 s at every bar boundary (measured 2026-09-21 at the
# M5 boundary: both feeds None from +0.02 s to +0.47 s). That closed two TM
# trades on false M15 bias flips (07:15:00 and 14:45:00: the standing M15
# CISD read as missing, so the older ATR event won the bias). The grace
# below covers the republish latency; it only delays declaring a feed dead
# by that much.
BAR_STALENESS_MULTIPLIER = 2.0
BAR_STALENESS_GRACE_SECONDS = 30.0


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
    max_bar_age = tf_minutes * 60 * BAR_STALENESS_MULTIPLIER + BAR_STALENESS_GRACE_SECONDS
    if bar_age > max_bar_age:
        print(f"[V7S-BRIDGE] {symbol} M{tf_minutes}: bar_time is {bar_age:.0f}s old "
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


# M1, M3, M5 and M15 ATR-dual and Supertrend data come STRICTLY from this bridge -- no
# internal (copy_rates) computation for them anywhere in V7S (user, 2026-09-21). A stale or
# missing bridge file means that source contributes nothing this cycle; there is deliberately
# no native fallback. Other timeframes (M30 and up, M10) are still computed natively.
BRIDGE_ONLY_TIMEFRAMES = (1, 3, 5, 15)


@dataclass(frozen=True)
class BridgeATR:
    """Both ATR-dual trail lines of one timeframe, exactly as the MQL5 indicator publishes them
    (trail value, that line's own trend, and when its trend last changed), plus the close and
    bar_time of the bridge's last closed bar."""
    line1: float
    line1_trend: int
    line1_event_time: int
    line2: float
    line2_trend: int
    line2_event_time: int
    close: float
    bar_time: int


@dataclass(frozen=True)
class BridgeSupertrend:
    supertrend: float
    trend: int
    event_time: int
    close: float
    bar_time: int


@dataclass(frozen=True)
class HammerStar:
    """Candle-pattern state for one timeframe, from
    HAMMERSTAR_<sym>_<tf>.json (mql5/ATR_Trial_Dual_SuperTrend_Major_Minor_HammerStar.mq5's
    own HammerShootingStar block). hammer/star are the LAST CLOSED bar's
    own shape -- True for that bar's entire own duration (until the NEXT
    bar closes, same "momentary but bar-scoped" contract cisd_bridge's
    fresh_cisd() uses), not a standing memory. last_hammer_time/
    last_star_time are the standing memory (most recent occurrence of
    each, 0 if none yet) -- used by exit_manager_candle.py (EA-CandleExit,
    component 3) only for logging, never for the trigger itself."""
    hammer: bool
    star: bool
    last_hammer_time: int
    last_star_time: int
    close: float
    bar_time: int


def read_hammer_star(symbol: str, tf_minutes: int) -> Optional[HammerStar]:
    """This timeframe's candle-pattern state. None if the file is
    missing/stale/incomplete -- same two freshness tests every other
    reader here uses."""
    raw = _read_fresh(f"HAMMERSTAR_{symbol}_{tf_minutes}.json", symbol, tf_minutes)
    if raw is None:
        return None
    try:
        return HammerStar(hammer=bool(raw["hammer"]), star=bool(raw["star"]),
                          last_hammer_time=int(raw["last_hammer_time"]), last_star_time=int(raw["last_star_time"]),
                          close=float(raw["close"]), bar_time=int(raw["bar_time"]))
    except (KeyError, TypeError, ValueError):
        return None


def _read_fresh(filename: str, symbol: str, tf_minutes: int) -> Optional[dict]:
    """The parsed bridge file, or None if it is missing, unreadable or stale -- the same two
    freshness tests read_lines() uses (file touched recently AND bar_time keeping pace)."""
    try:
        raw = json.loads((_bridge_root() / filename).read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    if time.time() - raw.get("updated", 0) > MAX_AGE_SECONDS:
        return None
    if _bar_time_stale(raw, tf_minutes, symbol):
        return None
    return raw


def read_atr_dual(symbol: str, tf_minutes: int) -> Optional[BridgeATR]:
    """Both ATR-dual lines with their trend/event data from ATRSTATE_DUAL_<sym>_<tf>.json. None
    if the file is missing/stale/incomplete. Deliberately ignores the bundled "structure" field
    (see the module docstring)."""
    raw = _read_fresh(f"ATRSTATE_DUAL_{symbol}_{tf_minutes}.json", symbol, tf_minutes)
    if raw is None:
        return None
    try:
        l1, l2 = raw["line1"], raw["line2"]
        return BridgeATR(
            line1=float(l1["trail_stop"]), line1_trend=int(l1["trend"]), line1_event_time=int(l1["event_time"]),
            line2=float(l2["trail_stop"]), line2_trend=int(l2["trend"]), line2_event_time=int(l2["event_time"]),
            close=float(raw["close"]), bar_time=int(raw["bar_time"]))
    except (KeyError, TypeError, ValueError):
        return None


def read_supertrend(symbol: str, tf_minutes: int) -> Optional[BridgeSupertrend]:
    """The Supertrend line from SUPERTREND_<sym>_<tf>.json (published by the MQL5 Supertrend
    indicator). None if the file is missing/stale/incomplete."""
    raw = _read_fresh(f"SUPERTREND_{symbol}_{tf_minutes}.json", symbol, tf_minutes)
    if raw is None:
        return None
    try:
        return BridgeSupertrend(supertrend=float(raw["supertrend"]), trend=int(raw["trend"]),
                                event_time=int(raw["event_time"]), close=float(raw["close"]),
                                bar_time=int(raw["bar_time"]))
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
