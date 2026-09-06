"""Bridge-only flip detection for the STR Reversal Manager's M3/M1 LTF
confirmation triggers -- confirmed 2026-09-07: "for reversal confirmation
of LTF, dont refer LTF signal from copyrates, check on live data via
bridge only... flips to be taken from bridge data, not M3/M5 bullish
candles." So: M3 ATR flip and M1 flip now read the live MQL5 bridge
ONLY (no copy_rates fallback for these two triggers specifically) --
M5/M3 bullish-candle triggers are UNCHANGED, still copy_rates, since the
bridge publishes no open/close at all (confirmed by inspecting the actual
JSON schema: symbol/timeframe_minutes/updated/line1{trail_stop,trend,
event_time}/line2{...}/structure/structure_event_time -- no OHLC), so
there's no bridge equivalent for a candle-color check.

Direction is computed geometrically -- live price (mid of bid/ask)
against the bridge's own raw line1/line2.trail_stop VALUES, the exact
same test bridge.py's own Trend Manager tie-breaker already uses.
Deliberately NOT the bridge's bundled "structure" field, which measures
a different and already-proven-unreliable thing (see bridge.py's own
docstring for the 2026-09-04 false-positive this avoids repeating).

A "flip" is an EDGE -- the bridge-implied direction actually CHANGING
from what was last recorded for that timeframe -- not merely "currently
reads bullish/bearish" (the same event-vs-state distinction flip_state.py
draws between a fresh FLIP and an already-settled confirmed state).
State is persisted per timeframe so a bot restart doesn't rediscover the
current direction as a brand new "flip".

Staleness/ambiguity both read as "nothing to report" (None), same
fallback philosophy as bridge.py's reconcile() -- no live/reliable data
means no signal, never a guess.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from v5_sentinel import bridge


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
