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
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# A candidate trend flip must be seen on this many CONSECUTIVE polls before
# being committed -- see this module's own docstring.
_DEBOUNCE_POLLS = 2


@dataclass
class _TrendState:
    trend: int
    event_time: int


@dataclass
class _PendingFlip:
    trend: int    # the candidate trend value being confirmed
    streak: int   # consecutive polls it's been seen for, so far


class AtrTrendTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[str, _TrendState] = {}
        # Debounce state is intentionally NOT persisted to disk -- it only
        # ever spans a couple of poll cycles (seconds), so losing it across
        # a restart just means "start the confirmation count over," never a
        # wrong committed value.
        self._pending: dict[str, _PendingFlip] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str) -> str:
        return f"{symbol}|{timeframe}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._state = {k: _TrendState(**v) for k, v in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError):
            self._state = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({k: asdict(v) for k, v in self._state.items()}))

    def update(self, symbol: str, timeframe: str, computed_trend: int, now: int) -> tuple[int, int]:
        """Returns (trend, event_time) to write into AtrStore this poll.
        First sighting for this symbol/timeframe: seeds event_time at now
        immediately, no debounce -- we don't know the true last-flip time
        anyway, so "first time we noticed" is the best available proxy,
        same tradeoff first_seen_store.py makes for zones, and there's no
        prior committed value a false first-read could corrupt.

        Unchanged trend (matches the last COMMITTED value): event_time
        holds steady, and any in-progress pending flip is dropped -- price
        came back to the committed side before the flip ever confirmed.

        Changed trend: not committed immediately. Tracked as a pending
        flip; only becomes the new committed state (event_time = now, from
        the poll that completed the streak) once seen _DEBOUNCE_POLLS
        polls in a row. Until then, the previous committed (trend,
        event_time) is returned unchanged -- callers never see a
        not-yet-confirmed value."""
        key = self._key(symbol, timeframe)
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
