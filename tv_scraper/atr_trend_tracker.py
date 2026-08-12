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
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class _TrendState:
    trend: int
    event_time: int


class AtrTrendTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[str, _TrendState] = {}
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
        (we don't know the true last-flip time, so "first time we noticed"
        is the best available proxy, same tradeoff first_seen_store.py
        makes for zones). Unchanged trend: event_time holds steady.
        Changed trend: event_time advances to now."""
        key = self._key(symbol, timeframe)
        existing = self._state.get(key)
        if existing is None or existing.trend != computed_trend:
            self._state[key] = _TrendState(trend=computed_trend, event_time=now)
            self._save()
            return computed_trend, now
        return existing.trend, existing.event_time
