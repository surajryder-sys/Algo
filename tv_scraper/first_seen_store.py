"""Assigns each scraped zone a stable, real Unix timestamp the first time
it's seen, persisted so later polls reuse the same value instead of minting
a new one -- this is what scraper.py writes into ZoneStore as a zone's
`start_time`, since the Data Window never exposes a zone's true origin
candle time (see ob_detector_webhook.pine's comment on why start_time was
deliberately left out of its Data Window plots).

Without this, scraper.py's own price-derived zone identity (see
scraper._zone_key) would end up written as `start_time` directly -- a
number roughly 400,000x smaller than a real Unix timestamp, which silently
breaks every downstream comparison that assumes start_time is a real time
(algo_v2_tv_xauusd's freshness checks, and its base36 order-comment
encoding, which collapses any value at or below its 2025 epoch to a single
colliding "0" digit).

Keyed by (symbol, timeframe, direction, price_key) -- price_key is
scraper._zone_key(zone)'s existing rounded-top-price identity, used here
purely as a lookup key, not written anywhere downstream itself.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class FirstSeenStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._seen: dict[str, int] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str, direction: str, price_key: int) -> str:
        return f"{symbol}|{timeframe}|{direction}|{price_key}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._seen = {k: int(v) for k, v in json.loads(self._path.read_text()).items()}
        except (json.JSONDecodeError, OSError, ValueError):
            self._seen = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._seen))

    def get_or_create(self, symbol: str, timeframe: str, direction: str, price_key: int) -> int:
        """Returns the stable first-seen timestamp for this zone, assigning
        `now` the first time this exact price_key is seen for this
        symbol/timeframe/direction, and reusing it on every later call."""
        key = self._key(symbol, timeframe, direction, price_key)
        existing = self._seen.get(key)
        if existing is not None:
            return existing
        now = int(time.time())
        self._seen[key] = now
        self._save()
        return now

    def forget(self, symbol: str, timeframe: str, direction: str, price_key: int) -> None:
        """Call once a zone is confirmed mitigated -- frees its price_key so
        a genuinely new zone that later forms at a coincidentally similar
        price (support/resistance levels do recur) gets its own fresh
        first-seen time instead of inheriting this one's."""
        key = self._key(symbol, timeframe, direction, price_key)
        if self._seen.pop(key, None) is not None:
            self._save()
