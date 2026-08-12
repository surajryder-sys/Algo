"""Tracks whether/when a zone has been RETESTED -- price re-entering its
[btm, top] range at any point after formation -- as distinct from
"mitigated" (LuxAlgo's own full-invalidation, which removes the zone from
the chart's array/Data Window entirely).

This mirrors OB_ATR_Bridge_Indicator_v1.00.mq5's own definition exactly:
`zones[i].virgin = !visited`, where `visited` comes from a dedicated
HasZoneBeenRetested() check (any later candle's high/low range overlapping
[z.low, z.high], skipping the detection bar itself) -- NOT from whether the
zone has been removed from the indicator's own tracking. Confirmed live:
the TradingView pipeline was reporting a freshly-touched-but-not-yet-fully-
invalidated M1 bull zone as virgin=True, because it was really tracking
"not yet mitigated" instead.

Stores WHEN each zone was first observed retested (not just whether), so
that timestamp can flow through to TVZone.retested_at for the trading
logic to use -- same "first observed, not true origin" tradeoff already
made for zone start_time (see first_seen_store.py): the exact bar a retest
happened on is a raw Pine bar_time value, which would break this chart's
price-scale autoscale the same way raw zone start_time once did if plotted
directly, so it's never exposed that way. "Wall-clock moment tv_scraper
(or the alert path) first noticed it" is the best available proxy.

Two ways a retest gets recorded, combined additively (never downgraded):
  - live-Close approximation (this module's own check()) -- Close read off
    the Data Window happened to fall inside [btm, top] on some poll.
  - Pine's own wick-based check (OBD_SecretTrader.pine's mark_retests(),
    exposed as the "Retested" 1/0 Data Window plots) -- authoritative,
    since it sees every bar's real high/low, not just whatever Close read
    at poll time. Recorded via mark().
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


class RetestTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._retested_at: dict[str, int] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str, direction: str, price_key: int) -> str:
        return f"{symbol}|{timeframe}|{direction}|{price_key}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._retested_at = {k: int(v) for k, v in json.loads(self._path.read_text()).items()}
        except (json.JSONDecodeError, OSError, ValueError):
            self._retested_at = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._retested_at))

    def check(self, symbol: str, timeframe: str, direction: str, price_key: int,
              close: Optional[float], btm: float, top: float,
              is_first_sighting: bool) -> Optional[int]:
        """Returns the retested_at timestamp if this zone has been retested
        (ever, including just now via this check), else None.
        is_first_sighting: True on the poll that first detected this zone
        -- skipped, same as the MT5 indicator excluding the detection bar
        itself, so a zone isn't marked retested by the very price action
        that revealed it. close=None (Data Window's Close wasn't parsed
        this poll) just returns whatever was already known, same fail-soft
        fallback used elsewhere in this module."""
        key = self._key(symbol, timeframe, direction, price_key)
        existing = self._retested_at.get(key)
        if existing is not None:
            return existing
        if close is not None and not is_first_sighting and btm <= close <= top:
            now = int(time.time())
            self._retested_at[key] = now
            self._save()
            return now
        return None

    def mark(self, symbol: str, timeframe: str, direction: str, price_key: int) -> int:
        """Unconditionally records this zone as retested (used when Pine's
        own wick-based check reports true) and returns the retested_at
        timestamp -- the existing one if already recorded (never
        downgraded/overwritten), otherwise now."""
        key = self._key(symbol, timeframe, direction, price_key)
        existing = self._retested_at.get(key)
        if existing is not None:
            return existing
        now = int(time.time())
        self._retested_at[key] = now
        self._save()
        return now

    def forget(self, symbol: str, timeframe: str, direction: str, price_key: int) -> None:
        """Call once a zone is confirmed mitigated -- see first_seen_store.py's
        forget() for why (a future zone at a coincidentally similar price
        must not inherit this one's retest status)."""
        key = self._key(symbol, timeframe, direction, price_key)
        if key in self._retested_at:
            del self._retested_at[key]
            self._save()
