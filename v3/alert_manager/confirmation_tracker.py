"""Closes the staleness gap between Alert Manager's 1s MT5 price checks
and tv_scraper's own much slower (20-60s+ per timeframe, confirmed live)
poll cadence -- see project_v3_crypto_architecture memory for the full
mechanism. Without this, Alert Manager could act on a single tv_scraper
snapshot that's already stale relative to the real chart (e.g. a zone
that's genuinely left the visible top-4 boxes, but tv_scraper's own
2-consecutive-miss mitigation debounce hasn't caught up yet).

Requires a zone to be virgin across at least 2 DISTINCT tv_scraper
writes (not just 2 Alert Manager polls, which run far more often than
tv_scraper actually refreshes) before Alert Manager trusts it enough to
alert on. Detects a genuine new tv_scraper write via the zone file's own
mtime -- ZoneStore._save() writes unconditionally on every poll
regardless of whether content changed, so a real write always advances
mtime; re-reading the same unchanged file within that window does not.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


class ConfirmationTracker:
    def __init__(self):
        self._last_mtime: dict[str, float] = {}
        self._prev_virgin_keys: dict[str, set] = {}
        self._curr_virgin_keys: dict[str, set] = {}

    def update(self, symbol: str, path: str, virgin_keys_now: set) -> None:
        """Call once per Alert Manager cycle, before checking eligibility
        for this symbol. Only advances the confirmation window when the
        zone file's mtime has genuinely moved forward since the last
        call -- a no-op otherwise, so re-reading unchanged data between
        real tv_scraper writes doesn't spuriously "confirm" anything."""
        mtime = self._read_mtime(path)
        if mtime is None:
            return
        last = self._last_mtime.get(symbol)
        if last is not None and mtime <= last:
            return
        self._last_mtime[symbol] = mtime
        self._prev_virgin_keys[symbol] = self._curr_virgin_keys.get(symbol, set())
        self._curr_virgin_keys[symbol] = virgin_keys_now

    @staticmethod
    def _read_mtime(path: str) -> Optional[float]:
        try:
            return Path(path).stat().st_mtime
        except OSError:
            return None

    def is_confirmed(self, symbol: str, key: str) -> bool:
        """True only if `key` was virgin in BOTH the current and the
        immediately preceding distinct tv_scraper write for this symbol
        -- i.e. it's been stable across at least 2 real refreshes, not
        just a single snapshot that might already be behind reality."""
        return (key in self._prev_virgin_keys.get(symbol, set())
                and key in self._curr_virgin_keys.get(symbol, set()))
