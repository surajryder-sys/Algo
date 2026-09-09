"""Bridge-sourced M3 far/near line reads and staleness tracking for the
STR Reversal Manager, plus the shared StaleAlertTracker other bots reuse
too. The live-tick M3 flip DETECTION that used to live here
(BridgeFlipState) was retired 2026-09-09 -- reversal_entry.py now uses
bridge_bar_flip.BridgeBarFlipTracker instead (the same bar-close-gated
state machine Trend Manager runs), per the user's own direction: "live
tick only for higher timeframe touch analysis, decision and execution
analysis is based on m3 which is bar close analysis." See that module
and reversal_entry.py's own docstrings for the full reasoning.

User's own words (2026-09-07, on why this whole module reads the bridge
directly rather than falling back to copy_rates): "remove dependancy of
copy rates for M5,M3,M1 -- follow exactly bridge, nothing else. if any
data is stale on bridge for more than some specified time, simple send
message saying data is stale, so i can manually check on chart,
simple." So: no fallback, no guessing -- StaleAlertTracker below fires
an alert once a timeframe's bridge data has been stale/missing for
longer than STALE_ALERT_THRESHOLD_SECONDS, and re-arms (can alert
again) once fresh data returns.

NOTE on scope: far_near()/m3_far_line() below are the M3 far-line SL
basis for RM's ONGOING (post-breakeven) trailing specifically -- a live
read is correct there, since trailing SL should keep moving with the
current line, unlike the INITIAL entry SL basis (frozen at the flip's
own candle, see reversal_entry.py). M1's swing-low/high SL basis is
UNCHANGED, still copy_rates -- the bridge publishes no OHLC at all
(confirmed against the actual JSON schema: symbol/timeframe_minutes/
updated/line1{trail_stop,trend,event_time}/line2{...}/structure/
structure_event_time), so there is no bridge alternative for real bar
highs/lows to fall back to.
"""
from __future__ import annotations

import time
from typing import Optional

from v5_sentinel import bridge, flip_state

STALE_ALERT_THRESHOLD_SECONDS = 60.0  # sustained staleness, not a momentary blip, before alerting


class StaleAlertTracker:
    """Tracks how long each timeframe's bridge data has been continuously
    stale/missing (in-memory only -- a restart re-arming this is fine,
    it just means one fresh 60s grace period again). Fires an alert
    message once past STALE_ALERT_THRESHOLD_SECONDS, ONCE per staleness
    episode (not every cycle -- that would spam), and re-arms the moment
    fresh data returns so a LATER staleness episode alerts again."""

    def __init__(self):
        self._stale_since: dict[int, float] = {}
        self._alerted: dict[int, bool] = {}

    def check(self, tf_minutes: int, available: bool) -> Optional[str]:
        if available:
            self._stale_since.pop(tf_minutes, None)
            self._alerted.pop(tf_minutes, None)
            return None

        now = time.time()
        since = self._stale_since.setdefault(tf_minutes, now)
        if not self._alerted.get(tf_minutes) and (now - since) > STALE_ALERT_THRESHOLD_SECONDS:
            self._alerted[tf_minutes] = True
            return (f"[V5S-STR-ALERT] M{tf_minutes} bridge data has been stale/missing for over "
                   f"{STALE_ALERT_THRESHOLD_SECONDS:.0f}s -- please check the M{tf_minutes} chart manually.")
        return None


def far_near(symbol: str, tf_minutes: int, direction: int) -> Optional[tuple[float, float]]:
    """(far, near) for one timeframe's own two lines, sourced from the
    bridge's raw values -- bridge-only, no copy_rates fallback. None if
    that timeframe's bridge data is missing/stale, which the caller must
    treat as "skip this cycle", not a guess. Used for: RM's "3F" SL
    basis, RM/TM's post-breakeven trailing (far), and TM's watch-zone
    pullback target (near)."""
    lines = bridge.read_lines(symbol, tf_minutes)
    if lines is None:
        return None
    return flip_state.far_near_line(direction, lines[0], lines[1])


def m3_far_line(symbol: str, direction: int) -> Optional[float]:
    """M3's far trail line only -- thin wrapper over far_near() for
    callers (RM's SL basis, post-breakeven trailing) that only need the
    far side."""
    result = far_near(symbol, 3, direction)
    return None if result is None else result[0]
