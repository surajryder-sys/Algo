"""CISD (Change in State of Delivery) bridge reader. Confirmed with the
user 2026-09-15: a ported AlgoAlpha CISD indicator (mql5/CISD_AlgoAlpha.mq5)
is already attached and publishing on M1/M3/M5/M15 -- CISD_{symbol}_{tf}.json,
same OBBridge Common Files folder every other bridge reader here uses. "we
will be using entry logic based on CISD for the algo, only for reversal
managers" -- confirmed scoped to RM-STR/RM-ICT only (reversal_entry.py/
reversal_ict.py), NOT TM-STR/TM-ICT.

DESIGN (confirmed 2026-09-15 via three rounds of questions):
  - ADDS ALONGSIDE the existing ST1F/M3F/ST3F/M5F triggers, doesn't
    replace them -- same "any one sufficient" additive pattern.
  - M3 and M5 ONLY (not M1, not M15) -- tags "M3CD"/"M5CD" ("CD" = CISD,
    matching this project's short trigger-code convention).
  - NO M15 Primary Structure gate -- a CISD confirmation on its own
    timeframe is sufficient to fire on its own, independent of M15's
    current bias (unlike ST1F/M3F/ST3F/M5F, which are all gated by it).

SL BASIS (confirmed 2026-09-15, a separate follow-up round): NOT
last_cisd_level (the origin candle's own open, i.e. the exact price
CISD confirms THROUGH -- "cuz they are very early") -- "lets take low
of cisd candle with buffer? or recent swing low??" -- picked "Nearest
active swing low/high" over the confirming candle's own low/high:
wider, structural, ties the stop to the swing the reversal is actually
trading against rather than a tight technical level price sits right
back on. Sourced from CISD_AlgoAlpha.mq5's own two new bridge fields
(last_cisd_has_swing/last_cisd_swing_level, added same day) -- FROZEN
at the exact instant the CISD confirmed (the front/newest entry of the
indicator's own sh_level[]/sl_level[] arrays at that moment), never
live-recomputed here. has_swing can genuinely be False (no active swing
line existed yet) -- no fallback, no guess, that CISD trigger simply
produces no signal this cycle, same philosophy every other trigger here
already follows.

Bar-close-gated on the MQL5 side already, same bridge-is-ground-truth
philosophy st_bridge.py's own reader uses -- no independent recompute
here. fresh_cisd() mirrors st_bridge.fresh_flip()'s own "privileged,
momentary" contract exactly: only non-None the EXACT bar a CISD
confirmed (last_cisd_time == bar_time), never an older-but-still-current
CISD state.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from v5_sentinel.bridge import MAX_AGE_SECONDS, _bar_time_stale, _bridge_root

_CISD_DIRECTION = {"bullish": 1, "bearish": -1}


@dataclass(frozen=True)
class CISDState:
    symbol: str
    timeframe_minutes: int
    trend: int                # 1 bullish, -1 bearish, 0 none yet
    last_cisd: str              # "bullish" | "bearish" | "none"
    last_cisd_level: float        # the confirmed CISD's own origin price (the reversal candle's open) -- NOT the SL basis, see module docstring's own SL BASIS section
    last_cisd_time: int             # bar_time of the bar that confirmed it
    last_cisd_sweep: bool             # whether it followed a liquidity-sweep wick mitigation
    last_cisd_has_swing: bool           # whether an active swing low/high existed at confirmation time -- False means no valid SL basis, caller must not fire
    last_cisd_swing_level: float          # the SL basis: nearest active swing low (bullish) / high (bearish), frozen at confirmation
    close: float                            # close of the last CLOSED bar
    bar_time: int                             # that bar's own time


def read_cisd(symbol: str, tf_minutes: int) -> Optional[CISDState]:
    """None if the file is missing, unreadable, stale (file not touched
    recently, or its own bar_time has fallen behind real bar closes --
    same two-tier staleness check every other bridge reader here uses),
    or last_cisd is "none" (no CISD has EVER confirmed on this timeframe
    since the indicator was attached/reset -- nothing to report yet, not
    an error)."""
    path = _bridge_root() / f"CISD_{symbol}_{tf_minutes}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, KeyError):
        return None

    age = time.time() - raw.get("updated", 0)
    if age > MAX_AGE_SECONDS:
        return None
    if _bar_time_stale(raw, tf_minutes, symbol):
        return None

    last_cisd = raw.get("last_cisd")
    if last_cisd not in _CISD_DIRECTION:
        return None

    try:
        return CISDState(
            symbol=symbol, timeframe_minutes=tf_minutes,
            trend=int(raw["trend"]), last_cisd=last_cisd,
            last_cisd_level=float(raw["last_cisd_level"]),
            last_cisd_time=int(raw["last_cisd_time"]),
            last_cisd_sweep=bool(raw["last_cisd_sweep"]),
            last_cisd_has_swing=bool(raw["last_cisd_has_swing"]),
            last_cisd_swing_level=float(raw["last_cisd_swing_level"]),
            close=float(raw["close"]), bar_time=int(raw["bar_time"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def fresh_cisd(symbol: str, tf_minutes: int) -> Optional[CISDState]:
    """The CISDState only if it confirmed on the CURRENT/latest closed
    bar (last_cisd_time == bar_time) -- same "privileged, momentary"
    contract as st_bridge.fresh_flip(): an OLDER CISD that's still the
    current trend but didn't just confirm THIS bar does NOT count, so
    this never re-fires once a bar passes without a fresh confirmation.
    None if there's no fresh confirmation, or the bridge has nothing to
    offer at all right now."""
    cisd = read_cisd(symbol, tf_minutes)
    if cisd is None or cisd.last_cisd_time != cisd.bar_time:
        return None
    return cisd


def direction_of(cisd: CISDState) -> int:
    """1 for a bullish CISD, -1 for a bearish one -- last_cisd is always
    one of the two by the time fresh_cisd() has returned non-None (see
    read_cisd()'s own "none" filter above), so this never needs a
    fallback."""
    return _CISD_DIRECTION[cisd.last_cisd]


def sl_basis(cisd: CISDState) -> Optional[float]:
    """The nearest active swing low/high, frozen at confirmation time --
    see module docstring's own SL BASIS section. None if no active swing
    line existed at that moment (last_cisd_has_swing is False) -- no
    fallback, no guess; the caller must treat that as "this trigger
    can't fire right now", same as a stale/missing bridge file."""
    return cisd.last_cisd_swing_level if cisd.last_cisd_has_swing else None
