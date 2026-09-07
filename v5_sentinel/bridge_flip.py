"""Bridge-ONLY flip detection for the STR Reversal Manager's M3/M1 LTF
confirmation triggers. Originally built bridge-first/copy_rates-fallback
(2026-09-07 morning), then changed to bridge-only, no fallback at all,
same day: a live incident (H4/3F SELL immediately reversed into H8/3F
BUY just 9 seconds later -- impossible for a genuine M3 flip, bars close
every 3 REAL minutes) exposed a real design flaw in the fallback --
BridgeFlipState (this file) and the old copy_rates path
(flip_state.event_just_happened()) used completely different edge-
detection logic with no shared dedup between them. If the system ever
switched sources mid-stream (bridge crossing the staleness cutoff right
at the boundary), a stale-but-still-"just happened" bar on one path
could re-trigger independently of the other path's own state. Going
bridge-only removes that whole class of bug by construction, whether or
not this exact incident was that specific mechanism.

User's own words (2026-09-07): "remove dependancy of copy rates for
M5,M3,M1 -- follow exactly bridge, nothing else. if any data is stale on
bridge for more than some specified time, simple send message saying
data is stale, so i can manually check on chart, simple." So: no
fallback, no guessing -- StaleAlertTracker below fires an alert once a
timeframe's bridge data has been stale/missing for longer than
STALE_ALERT_THRESHOLD_SECONDS, and re-arms (can alert again) once fresh
data returns. M5 isn't listed here because Reversal Manager doesn't use
an M5 signal at all any more (candle triggers were removed the same
day) -- nothing to monitor there for this bot.

NOTE on scope: this is about SIGNAL detection (the flip itself) and the
M3 far-line SL basis (m3_far_line() below, also switched to bridge-only
for consistency). M1's swing-low/high SL basis is UNCHANGED, still
copy_rates -- the bridge publishes no OHLC at all (confirmed against the
actual JSON schema: symbol/timeframe_minutes/updated/line1{trail_stop,
trend,event_time}/line2{...}/structure/structure_event_time), so there
is no bridge alternative for real bar highs/lows to fall back to.

Direction is computed geometrically -- live price (mid of bid/ask)
against the bridge's own raw line1/line2.trail_stop VALUES, the exact
same test bridge.py's own Trend Manager tie-breaker already uses.
Deliberately NOT the bridge's bundled "structure" field, which measures
a different and already-proven-unreliable thing (see bridge.py's own
docstring for the 2026-09-04 false-positive this avoids repeating).

A "flip" is an EDGE -- the bridge-implied direction actually CHANGING
from what was last recorded for that timeframe -- not merely "currently
reads bullish/bearish". State is persisted per timeframe so a bot
restart doesn't rediscover the current direction as a brand new "flip".
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from v5_sentinel import bridge, flip_state

STALE_ALERT_THRESHOLD_SECONDS = 60.0  # sustained staleness, not a momentary blip, before alerting


class BridgeFlipState:
    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[int, int] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._state = {int(k): v for k, v in data.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._state = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._state))

    def check(self, symbol: str, tf_minutes: int, bid: float, ask: float) -> Optional[int]:
        """Returns the direction (1/-1) just flipped INTO this call, or
        None if nothing changed (including: bridge data missing/stale, or
        its own lines are themselves straddling live price -- ambiguous,
        same as bridge.py's own convention)."""
        lines = bridge.read_lines(symbol, tf_minutes)
        if lines is None:
            return None

        lo, hi = min(lines), max(lines)
        mid = (bid + ask) / 2
        if mid > hi:
            direction = 1
        elif mid < lo:
            direction = -1
        else:
            return None  # ambiguous -- between the lines right now

        prev = self._state.get(tf_minutes)
        if prev != direction:
            self._state[tf_minutes] = direction
            self._save()
        return direction if (prev is not None and prev != direction) else None


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
