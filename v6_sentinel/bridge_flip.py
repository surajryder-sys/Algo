"""Bridge-sourced far/near line reads and staleness tracking, ported
from v5_sentinel/bridge_flip.py (2026-09-18).

No fallback, no guessing: everything here reads the bridge directly
(never copy_rates) -- if a timeframe's bridge data is stale/missing,
StaleAlertTracker below fires an alert once that's sustained past
STALE_ALERT_THRESHOLD_SECONDS, and re-arms (can alert again) once fresh
data returns.

Multi-instrument note: StaleAlertTracker's in-memory dicts are keyed by
tf_minutes only, same as V5S -- this is fine ONLY because each symbol
gets its OWN tracker instance (V6S's symbol-agnostic-instance pattern,
see config.py), never one tracker instance shared across symbols. A
caller wiring this up for multiple symbols must instantiate one
StaleAlertTracker per symbol.

far_near() below is the far-line SL basis for ONGOING (post-breakeven)
trailing specifically -- a live read is correct there, since trailing SL
should keep moving with the current line, unlike an INITIAL entry SL
basis (fixed at the moment the entry signal fired, see sl_basis.py).
"""
from __future__ import annotations

import time
from typing import Optional

from v6_sentinel import bridge, flip_state

STALE_ALERT_THRESHOLD_SECONDS = 60.0  # sustained staleness, not a momentary blip, before alerting


class StaleAlertTracker:
    """Tracks how long each timeframe's bridge data has been continuously
    stale/missing (in-memory only -- a restart re-arming this is fine,
    it just means one fresh 60s grace period again). Fires an alert
    message once past STALE_ALERT_THRESHOLD_SECONDS, ONCE per staleness
    episode (not every cycle -- that would spam), and re-arms the moment
    fresh data returns so a LATER staleness episode alerts again. One
    instance per symbol -- see module docstring."""

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
            return (f"[V6S-STR-ALERT] M{tf_minutes} bridge data has been stale/missing for over "
                   f"{STALE_ALERT_THRESHOLD_SECONDS:.0f}s -- please check the M{tf_minutes} chart manually.")
        return None


def far_near(symbol: str, tf_minutes: int, direction: int) -> Optional[tuple[float, float]]:
    """(far, near) for one timeframe's own two lines, sourced from the
    bridge's raw values -- bridge-only, no copy_rates fallback. None if
    that timeframe's bridge data is missing/stale, which the caller must
    treat as "skip this cycle", not a guess."""
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
