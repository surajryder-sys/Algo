"""CISD (Change in State of Delivery) bridge reader. Ported verbatim from
v5_sentinel/cisd_bridge.py (2026-09-18) -- already fully symbol-
parametrized and stateless. Reads a ported AlgoAlpha CISD indicator
(mql5/CISD_AlgoAlpha.mq5), published on M1/M3/M5/M15 as
CISD_{symbol}_{tf}.json, same OBBridge Common Files folder every other
bridge reader here uses. Scoped to Reversal Manager components only
(RM-STR/RM-ICT), not Trend Manager.

HOW IT'S USED (current V7S design -- see reversal_entry.py and
reversal_ict.py):
  - CISD confirmation is the ENTRY TRIGGER for both Reversal Manager
    components: a fresh confirmation (fresh_cisd()) in the matching
    direction after a level/zone touch. RM-STR listens on M3 or M5;
    RM-ICT's pool depends on the touched zone's timeframe (M3/M5 for
    H4/H2/H1/M30/M15 zones, M1/M3/M5 for M5 zones -- M3 zones
    themselves were removed 2026-09-22, see ob_levels.py). Trigger tags
    are "M1CD"/"M3CD"/"M5CD" ("CD" = CISD).
  - There is no M15 Primary Structure gate and no other trigger type any
    more -- the older ST1F/M3F/ST3F/M5F triggers are gone.

SL BASIS: last_cisd_level (the origin candle's own open, i.e. the exact
price CISD confirms THROUGH) is NOT used -- too early/easy to stop-hunt.
The "nearest active swing low/high" (sl_basis() below) is the LAST-RESORT
SL basis: sl_basis.py tries the farthest usable M5 line, then M3, and only
then this swing. Sourced from CISD_AlgoAlpha.mq5's own
last_cisd_has_swing/last_cisd_swing_level fields -- FROZEN at the exact
instant the CISD confirmed, never live-recomputed here. has_swing can
genuinely be False (no active swing line existed yet) -- sl_basis()
then returns None and, if no line was usable either, that CISD produces
no signal this cycle (RM-STR) -- no fallback, no guess.

Bar-close-gated on the MQL5 side already, same bridge-is-ground-truth
philosophy as this project's other bridge readers -- no independent
recompute here. fresh_cisd() has a "privileged, momentary" contract:
only non-None the EXACT bar a CISD confirmed (last_cisd_time ==
bar_time), never an older-but-still-current CISD state.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from v7_sentinel.bridge import MAX_AGE_SECONDS, _bar_time_stale, _bridge_root

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
    bar (last_cisd_time == bar_time) -- a "privileged, momentary"
    contract: an OLDER CISD that's still the
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


def sl_basis(cisd: Optional[CISDState]) -> Optional[float]:
    """The nearest active swing low/high, frozen at confirmation time --
    see module docstring's own SL BASIS section. None if no active swing
    line existed at that moment (last_cisd_has_swing is False) -- no
    fallback, no guess; the caller must treat that as "this trigger
    can't fire right now", same as a stale/missing bridge file. Also None
    for cisd=None itself (2026-09-24, added for reversal_ict.py's own
    M1FLIP-triggered signals, which have no real CISD event of their own
    and pass a best-effort M3 standing CISD here purely as a swing-basis
    provider -- that read can legitimately come back None if M3's own
    bridge is stale, and this must degrade to "no valid override" rather
    than crash)."""
    return cisd.last_cisd_swing_level if cisd is not None and cisd.last_cisd_has_swing else None
