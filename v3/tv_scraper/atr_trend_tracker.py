"""Derives ATR trail trend/event_time from data tv_scraper already reads
correctly (trail_stop + live Close) instead of needing a dedicated "Trend"
plot on the chart at all.

trend is exactly "is price above or below the trail line" -- the same
condition the Pine script's own `pos` flips on (crossover/crossunder of
close vs the trail line) -- so close > trail_stop is a direct, live-
computable stand-in for it, with no chart changes required.

event_time (the bar time of the most recent Strong<->Weak flip) has no
live-computable equivalent -- the Data Window doesn't expose bar times,
same reason zones don't get a real start_time either (see
first_seen_store.py). This tracks the same "stable, persisted timestamp"
pattern: reuse the last-recorded event_time as long as trend hasn't
changed, and only advance it on an actual flip.

BAR-CLOSE confirmation (rewritten 2026-08-31, replacing the previous
2-consecutive-poll debounce entirely): confirmed live that a poll-count
debounce (~10 seconds for a 5s poll interval) is nowhere near enough to
protect against real intrabar noise on a 30-MINUTE bar -- BTCUSD's M30
"confirmed STRONG" once purely because live price poked above both trail
lines for two consecutive 5-second polls mid-candle, with the actual
30-minute bar nowhere near closing yet ("how did M30 confirm strong").
computed_trend is derived from live Close vs trail_stop, read fresh every
poll (see run_once_pane), with no confirmed-bar gating in the OLD
mechanism at all.

The fix: track each line's own bar boundary (derived from wall-clock time
and the timeframe's own duration -- e.g. M30 bars start every 1800
seconds past the Unix epoch, matching standard UTC-aligned exchange bar
alignment) and the latest computed_trend seen so far within that bar.
Nothing commits until wall-clock time actually crosses into a NEW bar --
at that moment, the JUST-CLOSED bar's last-seen computed_trend is what
gets committed (if it differs from the existing trend), stamped with that
bar's own real close time as event_time. A flip is now only ever
recognized once per real bar, on that bar's actual close, never on
intrabar noise -- the same "flip candle, not any random candle" standard
already used for XAUUSD's own MT5-native M1 flip detection, now applied
here as well. This affects EVERY consumer of this shared tracker
uniformly (M30/M15 parent bias AND M5's own confirmation, for both
crypto_trend_manager and usoil_ustec_trend_manager) -- not something
either of those packages needed to change on their own end.

Bar-tracking state (bar_start/pending_trend) IS persisted now, unlike the
old debounce counter -- losing it across a restart used to cost at most
~10 seconds (two 5-second polls); now it could cost up to a full bar's
worth of tracking (30 minutes for M30), which is worth not losing.

Two independent lines (2026-08-27): the chart's ATR indicator
(pine/OBD_ATR.pine) plots two trail lines -- a fast one (default ATR
period 2) and a slow one (default ATR period 300) -- each with its own
independent trend. Per the user's explicit rule, these are NOT averaged
or one-overrides-the-other: each line gets its own fully independent
bar-confirmed trend via update_line() (same logic as the original
single-line `update()`, just keyed per line), and update_structure()
combines the two already-committed trends into one "structure" reading --
STRONG only when BOTH lines agree bullish, WEAK only when both agree
bearish, UNDECISIVE whenever they disagree (or either line has no reading
yet). This is deliberately a stricter, slower-to-commit signal than
either line alone: one line flipping without the other is real
information (the trend is contested), not noise to be smoothed over.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

LINES = ("line1", "line2")


def _bar_seconds(timeframe: str) -> Optional[int]:
    """Bar duration in seconds for a plain-minutes timeframe string
    ("1"/"5"/"15"/"30"/"60"/"120"/"240", already normalized by
    parser.py's _normalize_timeframe from any hour-style label). None for
    anything that doesn't parse as a plain integer (e.g. "D"/"W") -- not
    used by any real timeframe in this repo today, but update_line falls
    back to committing immediately rather than crashing on it."""
    try:
        return int(timeframe) * 60
    except ValueError:
        return None


def _bar_start(now: int, bar_seconds: int) -> int:
    """The start (UTC-epoch-aligned) of whichever bar `now` falls inside --
    standard exchange bar alignment for round-number periods, e.g. M30
    bars start on the hour and half-hour."""
    return (now // bar_seconds) * bar_seconds


@dataclass
class _TrendState:
    trend: int
    event_time: int


@dataclass
class _BarTracking:
    bar_start: int      # the bar boundary currently being tracked
    pending_trend: int  # latest computed_trend seen so far within that bar


@dataclass
class _StructureState:
    state: str    # "STRONG" | "WEAK" | "UNDECISIVE"
    event_time: int


class AtrTrendTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[str, _TrendState] = {}
        self._structure: dict[str, _StructureState] = {}
        self._bar_tracking: dict[str, _BarTracking] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str, line: str) -> str:
        return f"{symbol}|{timeframe}|{line}"

    @staticmethod
    def _structure_key(symbol: str, timeframe: str) -> str:
        return f"{symbol}|{timeframe}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            # New nested schema ({"lines": {...}, "combined": {...},
            # "bar_tracking": {...}}). An older file (pre-2026-08-31, or
            # the even older pre-2026-08-27 flat schema) simply won't have
            # "bar_tracking" -- raw.get(...) comes back empty and every
            # line just starts fresh bar-tracking on its next poll, same
            # one-time cold-start cost already accepted for the schema
            # upgrade before this one.
            self._state = {k: _TrendState(**v) for k, v in raw.get("lines", {}).items()}
            self._structure = {k: _StructureState(**v) for k, v in raw.get("combined", {}).items()}
            self._bar_tracking = {k: _BarTracking(**v) for k, v in raw.get("bar_tracking", {}).items()}
        except (json.JSONDecodeError, OSError, TypeError):
            self._state = {}
            self._structure = {}
            self._bar_tracking = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({
            "lines": {k: asdict(v) for k, v in self._state.items()},
            "combined": {k: asdict(v) for k, v in self._structure.items()},
            "bar_tracking": {k: asdict(v) for k, v in self._bar_tracking.items()},
        }))

    def update_line(self, symbol: str, timeframe: str, line: str, computed_trend: int, now: int) -> tuple[int, int]:
        """Returns (trend, event_time) for this one line -- see the module
        docstring for the full bar-close-confirmation contract."""
        key = self._key(symbol, timeframe, line)
        existing = self._state.get(key)

        if existing is None:
            # Cold start -- trust the first-ever reading immediately, same
            # as before. Also seeds bar tracking so the NEXT bar boundary
            # (not this partial one) is what next gets evaluated.
            self._state[key] = _TrendState(trend=computed_trend, event_time=now)
            bar_seconds = _bar_seconds(timeframe)
            if bar_seconds is not None:
                self._bar_tracking[key] = _BarTracking(bar_start=_bar_start(now, bar_seconds),
                                                        pending_trend=computed_trend)
            self._save()
            return computed_trend, now

        bar_seconds = _bar_seconds(timeframe)
        if bar_seconds is None:
            # Not a plain-minutes timeframe (shouldn't happen for any real
            # usage in this repo) -- commit immediately rather than never,
            # since there's no bar duration to wait on.
            if computed_trend != existing.trend:
                self._state[key] = _TrendState(trend=computed_trend, event_time=now)
                self._save()
                return computed_trend, now
            return existing.trend, existing.event_time

        tracking = self._bar_tracking.get(key)
        current_bar_start = _bar_start(now, bar_seconds)

        if tracking is None:
            # Loaded from a state file that predates bar_tracking -- start
            # fresh from THIS poll's bar, commit nothing yet (avoids
            # instantly "closing" a bar we never actually watched any of).
            self._bar_tracking[key] = _BarTracking(bar_start=current_bar_start, pending_trend=computed_trend)
            self._save()
            return existing.trend, existing.event_time

        if current_bar_start == tracking.bar_start:
            # Still inside the same bar -- just keep the latest reading on
            # hand for whenever it actually closes; nothing commits yet.
            if tracking.pending_trend != computed_trend:
                tracking.pending_trend = computed_trend
                self._save()
            return existing.trend, existing.event_time

        # Wall-clock time has crossed into a new bar -- the bar identified
        # by tracking.bar_start has now genuinely closed. Commit its last-
        # seen reading (if it actually differs from what's already
        # committed), stamped with that bar's own real close time -- not
        # `now` (which belongs to the NEW bar that just started).
        bar_close_time = tracking.bar_start + bar_seconds
        if tracking.pending_trend != existing.trend:
            self._state[key] = _TrendState(trend=tracking.pending_trend, event_time=bar_close_time)
            existing = self._state[key]
        # Start fresh tracking for the new (just-started, not-yet-closed) bar.
        self._bar_tracking[key] = _BarTracking(bar_start=current_bar_start, pending_trend=computed_trend)
        self._save()
        return existing.trend, existing.event_time

    def get_committed_trend(self, symbol: str, timeframe: str, line: str) -> Optional[int]:
        """Last-committed trend for one line, or None if never seen yet.
        Used to feed update_structure from the LAST KNOWN value for a line
        even on a poll where that specific line's plot didn't parse this
        time (a transient redraw gap) -- avoids the combined structure
        flickering to UNDECISIVE every time one line's read momentarily
        drops out while the other keeps updating."""
        existing = self._state.get(self._key(symbol, timeframe, line))
        return existing.trend if existing is not None else None

    def update_structure(self, symbol: str, timeframe: str, line1_trend: Optional[int],
                          line2_trend: Optional[int], now: int) -> tuple[str, int]:
        """Combines both lines' already bar-confirmed trends per the
        user's explicit rule: both agreeing is the only way to call it
        STRONG/WEAK; one flipped without the other (or a line not read
        even once yet) is UNDECISIVE. No separate confirmation needed
        here -- each input is already bar-confirmed by update_line, so
        this label only ever changes on an already-confirmed underlying
        flip; this just tracks WHEN the *combined* label itself last
        changed, same event_time contract as update_line."""
        if line1_trend is None or line2_trend is None:
            state = "UNDECISIVE"
        elif line1_trend == 1 and line2_trend == 1:
            state = "STRONG"
        elif line1_trend == -1 and line2_trend == -1:
            state = "WEAK"
        else:
            state = "UNDECISIVE"

        key = self._structure_key(symbol, timeframe)
        existing = self._structure.get(key)
        if existing is not None and existing.state == state:
            return existing.state, existing.event_time

        self._structure[key] = _StructureState(state=state, event_time=now)
        self._save()
        return state, now
