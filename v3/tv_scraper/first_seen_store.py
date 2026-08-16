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

get_or_create() accepts an optional `hint` -- a real timestamp
reconstructed by scraper.py from Pine's FormedSecondsAgo Data Window plot
(plain `now - seconds_ago`, Pine computing the elapsed seconds itself from
its own timenow -- see OBD_SecretTrader.pine's own comment on why that's
real seconds, not a raw timestamp). When given, and this is
genuinely the first sighting of this price_key, `hint` is stored as the
zone's first-seen time INSTEAD OF `now` -- letting a zone that already
existed before this scraper started watching (or before this exact
price_key first appeared, e.g. after a resurrection -- see
scraper._find_resurrectable()) get its true formation time rather than
"whenever this scraper first happened to notice it." Falls back to `now`
when no hint is available (indicator not updated yet, or na this poll),
same as before hints existed at all.
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

    def get_or_create(self, symbol: str, timeframe: str, direction: str, price_key: int,
                       hint: int | None = None) -> int:
        """Returns the stable first-seen timestamp for this zone, assigning
        a value the first time this exact price_key is seen for this
        symbol/timeframe/direction, and reusing it -- UNCHANGED -- on every
        later call.

        `hint`, when given, is used as that first-assigned value instead of
        `now` -- see this module's own docstring. Still runs through the
        same collision-avoidance bump as a plain `now` would (a hint is
        already rounded to the minute by scraper.py's _round_hint(), so two
        distinct zones forming within the same minute could otherwise
        collide exactly the way two same-second `now` calls could).

        Guaranteed unique within this (symbol, timeframe, direction) scope
        -- confirmed live (BTCUSD/M5): int(time.time()) only has 1-second
        resolution, and a single poll routinely first-sees several
        distinct zones at once (right after a restart, or during a burst
        of real formations), so a value handed out unmodified let two or
        three genuinely different zones collide on the identical
        timestamp. That's directly fatal downstream -- ZoneStore keys its
        per-direction dict BY this value, so a collision isn't just a
        cosmetic duplicate, it's a silent overwrite: whichever zone got
        processed last in that poll's loop won the dict slot and every
        earlier zone sharing the timestamp vanished from history with no
        error. Bumping forward one second at a time until a free slot is
        found costs at most a few seconds of drift from the true
        first-observed moment (already just an approximation) in exchange
        for never colliding."""
        key = self._key(symbol, timeframe, direction, price_key)
        existing = self._seen.get(key)
        if existing is not None:
            return existing
        prefix = f"{symbol}|{timeframe}|{direction}|"
        used = {v for k, v in self._seen.items() if k.startswith(prefix)}
        candidate = hint if hint is not None else int(time.time())
        while candidate in used:
            candidate += 1
        self._seen[key] = candidate
        self._save()
        return candidate

    def restore(self, symbol: str, timeframe: str, direction: str, price_key: int,
                start_time: int) -> None:
        """Re-establishes a first-seen entry at an EXACT known value --
        used by scraper._find_resurrectable() when a zone that was falsely
        read as mitigated (pure top-4 display churn, not a real LuxAlgo
        invalidation) reappears: reuses the resurrected ZoneStore entry's
        own real start_time directly, bypassing get_or_create()'s
        collision-bump entirely (this exact value already lived here
        before, and is by definition not colliding with anything still
        active right now)."""
        key = self._key(symbol, timeframe, direction, price_key)
        if self._seen.get(key) != start_time:
            self._seen[key] = start_time
            self._save()

    def forget(self, symbol: str, timeframe: str, direction: str, price_key: int) -> None:
        """Call once a zone is confirmed mitigated -- frees its price_key so
        a genuinely new zone that later forms at a coincidentally similar
        price (support/resistance levels do recur) gets its own fresh
        first-seen time instead of inheriting this one's."""
        key = self._key(symbol, timeframe, direction, price_key)
        if self._seen.pop(key, None) is not None:
            self._save()
