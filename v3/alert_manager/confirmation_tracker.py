"""Closes two separate staleness/reliability gaps between Alert Manager's
1s MT5 price checks and tv_scraper's own much slower (20-60s+ per
timeframe, confirmed live) poll cadence -- see
project_v3_crypto_architecture memory for the full mechanism.

1. Data-quality confirmation (original, 2026-08-17): a zone must be
   virgin across at least 2 DISTINCT tv_scraper writes (not just 2 Alert
   Manager polls, which run far more often than tv_scraper actually
   refreshes) before Alert Manager trusts it enough to alert on. Without
   this, Alert Manager could act on a single tv_scraper snapshot that's
   already stale relative to the real chart (e.g. a zone that's
   genuinely left the visible top-4 boxes, but tv_scraper's own
   2-consecutive-miss mitigation debounce hasn't caught up yet). Detects
   a genuine new tv_scraper write via the zone file's own mtime --
   ZoneStore._save() writes unconditionally on every poll regardless of
   whether content changed, so a real write always advances mtime;
   re-reading the same unchanged file within that window does not.

2. Visibility-stability confirmation (added 2026-08-17, later same day):
   even a data-quality-confirmed zone can fire correctly against a real
   snapshot and then get superseded/pushed out of the chart's visible
   top-4 boxes within minutes as newer zones form -- confirmed live
   across several reports (XAUUSD M30, ETHUSD H2, and XAUUSD H1/M15/M5
   firing simultaneously off one shared origin candle). Tracks how long,
   in continuous wall-clock seconds, a key has stayed in the eligible set
   without a gap -- a key that drops out for even one distinct write
   loses its clock and starts over if it reappears, since "continuously
   visible" is the property being checked, not cumulative appearances.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional


class ConfirmationTracker:
    def __init__(self):
        self._last_mtime: dict[str, float] = {}
        self._prev_virgin_keys: dict[str, set] = {}
        self._curr_virgin_keys: dict[str, set] = {}
        # symbol -> {key: wall-clock time this key was FIRST seen in an
        # unbroken run of eligible-set membership}.
        self._first_seen: dict[str, dict] = {}

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

        now = time.time()
        prev_first_seen = self._first_seen.get(symbol, {})
        # Carry forward the original first-seen time for keys still
        # present; anything not in virgin_keys_now this write is simply
        # omitted, so a later reappearance starts a fresh clock.
        self._first_seen[symbol] = {
            key: prev_first_seen.get(key, now) for key in virgin_keys_now
        }

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

    def visible_seconds(self, symbol: str, key: str) -> float:
        """How long `key` has been continuously present in the eligible
        set, in wall-clock seconds. 0.0 if not currently tracked."""
        first_seen = self._first_seen.get(symbol, {}).get(key)
        if first_seen is None:
            return 0.0
        return time.time() - first_seen

    def is_stable(self, symbol: str, key: str, min_visible_seconds: float) -> bool:
        """True once `key` has been continuously eligible for at least
        min_visible_seconds -- separate from (and checked in addition
        to) is_confirmed's data-quality check. Filters out zones that
        get touched and immediately superseded before there's any real
        chance to verify them against the live chart."""
        return self.visible_seconds(symbol, key) >= min_visible_seconds
