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

    def get_or_create(self, symbol: str, timeframe: str, direction: str, price_key: int,
                       hint: int | None = None) -> int:
        """Returns the stable first-seen timestamp for this zone, assigning
        a value the first time this exact price_key is seen for this
        symbol/timeframe/direction, and reusing it -- UNCHANGED -- on every
        later call, regardless of what's passed as `hint` on those later
        calls.

        hint: if the caller has something more accurate than "now" for a
        FIRST sighting (e.g. OBD_SecretTrader.pine's FormedBarsAgo plot,
        converted to a real timestamp -- see scraper.py), pass it here to
        seed the cache with that instead of the wall-clock guess. Ignored
        on every call after the first for a given price_key -- this is
        deliberate, not a bug: that value is recomputed by the CALLER from
        `now - bars_ago * timeframe_seconds` every poll, and drifts by a
        few seconds poll to poll (bars_ago only advances at bar
        boundaries, `now` doesn't). Using a fresh hint on every poll
        instead of caching it confirmed live (BTCUSD/M5): each poll's
        slightly different value read as a "new" zone to ZoneStore (which
        keys its history BY this exact value), fragmenting one real zone
        into five near-duplicate entries within seconds. Caching the
        first-seen hint and ignoring later ones keeps one real zone as
        one stable entry, same as the plain wall-clock path.

        Guaranteed unique within this (symbol, timeframe, direction) scope
        regardless of source -- confirmed live (BTCUSD/M5): int(time.time())
        only has 1-second resolution, and a single poll routinely first-sees
        several distinct zones at once (right after a restart, or during a
        burst of real formations), so a value handed out unmodified let two
        or three genuinely different zones collide on the identical
        timestamp. That's directly fatal downstream -- ZoneStore keys its
        per-direction dict BY this value, so a collision isn't just a
        cosmetic duplicate, it's a silent overwrite: whichever zone got
        processed last in that poll's loop won the dict slot and every
        earlier zone sharing the timestamp vanished from history with no
        error. Bumping forward one second at a time until a free slot is
        found costs at most a few seconds of drift from the true
        first-observed moment (already just an approximation when there's
        no hint) in exchange for never colliding."""
        key = self._key(symbol, timeframe, direction, price_key)
        existing = self._seen.get(key)
        if existing is not None:
            return existing
        prefix = f"{symbol}|{timeframe}|{direction}|"
        used = {v for k, v in self._seen.items() if k.startswith(prefix)}
        now = hint if hint is not None else int(time.time())
        while now in used:
            now += 1
        self._seen[key] = now
        self._save()
        return now

    def restore(self, symbol: str, timeframe: str, direction: str, price_key: int,
                start_time: int) -> None:
        """Directly (re-)establishes this price_key -> start_time mapping,
        bypassing the collision-avoidance bump get_or_create() normally
        does on a fresh guess -- used only when scraper.py has already
        confirmed via ZoneStore.get() that this EXACT start_time belongs
        to a real, previously-tracked zone reappearing after a false
        mitigation (top-4 display churn, not a genuine LuxAlgo removal --
        see _apply_direction's own comment), so there's no guessing here,
        just reasserting a value already known to be correct and free of
        collision risk (it was this exact price_key's own value before)."""
        key = self._key(symbol, timeframe, direction, price_key)
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
