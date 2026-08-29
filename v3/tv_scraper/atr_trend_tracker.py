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
changed, and only advance it to `now` on an actual flip.

A candidate flip must show up on _DEBOUNCE_POLLS consecutive polls before
it's actually committed -- same protection tv_scraper/scraper.py's
mitigation debounce added for zones, needed here for a different but
related reason: `computed_trend` is derived from live Close vs trail_stop
read fresh every poll (see run_once_pane), with no confirmed-bar gating at
all, so ordinary intrabar price noise near the trail line can cross it for
a single poll and cross back the very next one. Confirmed live on M1: a
poll read trend=+1 once, then trend=-1 again 26s later, with no real
2-candle move behind either -- undebounced, that permanently overwrote the
TRUE last flip's event_time with a fabricated one, even though the
committed trend value itself flipped right back and looked "correct"
again. Also protects against the pane-misread case (a stray poll reading
a completely different timeframe's pane under this timeframe's key --
confirmed live on H4 picking up a stray M15 read) the same way, since
that's just as transient as a single tick of price noise from this
tracker's point of view.

Two independent lines (2026-08-27): the chart's ATR indicator
(pine/OBD_ATR.pine) plots two trail lines -- a fast one (default ATR
period 2) and a slow one (default ATR period 300) -- each with its own
independent trend. Per the user's explicit rule, these are NOT averaged
or one-overrides-the-other: each line gets its own fully independent
debounced trend via update_line() (same logic as the original single-line
`update()`, just keyed per line), and update_structure() combines the two
already-committed trends into one "structure" reading -- STRONG only when
BOTH lines agree bullish, WEAK only when both agree bearish, UNDECISIVE
whenever they disagree (or either line has no reading yet). This is
deliberately a stricter, slower-to-commit signal than either line alone:
one line flipping without the other is real information (the trend is
contested), not noise to be smoothed over.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# A candidate flip (per line) must be seen on this many CONSECUTIVE polls
# before being committed -- see this module's own docstring.
_DEBOUNCE_POLLS = 2

LINES = ("line1", "line2")


@dataclass
class _TrendState:
    trend: int
    event_time: int


@dataclass
class _PendingFlip:
    trend: int    # the candidate trend value being confirmed
    streak: int   # consecutive polls it's been seen for, so far


@dataclass
class _StructureState:
    state: str    # "STRONG" | "WEAK" | "UNDECISIVE"
    event_time: int


class AtrTrendTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[str, _TrendState] = {}
        self._structure: dict[str, _StructureState] = {}
        # Debounce state is intentionally NOT persisted to disk -- it only
        # ever spans a couple of poll cycles (seconds), so losing it across
        # a restart just means "start the confirmation count over," never a
        # wrong committed value.
        self._pending: dict[str, _PendingFlip] = {}
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
            # New nested schema ({"lines": {...}, "combined": {...}}).
            # Pre-2026-08-27 files were a flat {"SYMBOL|TF": {trend,
            # event_time}} single-line schema -- raw.get("lines", {}) and
            # raw.get("combined", {}) both come back empty against that
            # old shape, so this just cold-starts (same as a missing file)
            # rather than crashing on it. Losing debounce/committed state
            # once across this one upgrade is an accepted, explicitly
            # one-time cost -- see the module docstring's same tradeoff
            # for _pending never being persisted at all.
            self._state = {k: _TrendState(**v) for k, v in raw.get("lines", {}).items()}
            self._structure = {k: _StructureState(**v) for k, v in raw.get("combined", {}).items()}
        except (json.JSONDecodeError, OSError, TypeError):
            self._state = {}
            self._structure = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({
            "lines": {k: asdict(v) for k, v in self._state.items()},
            "combined": {k: asdict(v) for k, v in self._structure.items()},
        }))

    def update_line(self, symbol: str, timeframe: str, line: str, computed_trend: int, now: int) -> tuple[int, int]:
        """Returns (trend, event_time) for this one line. Same debounce
        contract as the module's original single-line update() -- see the
        docstring above for the full first-sighting/unchanged/pending-flip
        rules, unchanged here except for being keyed per line now."""
        key = self._key(symbol, timeframe, line)
        existing = self._state.get(key)

        if existing is None:
            self._state[key] = _TrendState(trend=computed_trend, event_time=now)
            self._pending.pop(key, None)
            self._save()
            return computed_trend, now

        if computed_trend == existing.trend:
            self._pending.pop(key, None)
            return existing.trend, existing.event_time

        pending = self._pending.get(key)
        if pending is not None and pending.trend == computed_trend:
            pending.streak += 1
        else:
            pending = _PendingFlip(trend=computed_trend, streak=1)
            self._pending[key] = pending

        if pending.streak < _DEBOUNCE_POLLS:
            return existing.trend, existing.event_time

        self._state[key] = _TrendState(trend=computed_trend, event_time=now)
        self._pending.pop(key, None)
        self._save()
        return computed_trend, now

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
        """Combines both lines' already-debounced trends per the user's
        explicit rule: both agreeing is the only way to call it STRONG/WEAK;
        one flipped without the other (or a line not read even once yet)
        is UNDECISIVE. No separate debounce needed here -- each input is
        already debounced by update_line, so this label only ever changes
        on an already-confirmed underlying flip; this just tracks WHEN the
        *combined* label itself last changed, same event_time contract as
        update_line."""
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
