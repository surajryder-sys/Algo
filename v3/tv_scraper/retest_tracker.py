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

Two ways a retest gets recorded, tagged by SOURCE so one can correct the
other (see reconcile()):
  - live-Close approximation (this module's own check()) -- Close read off
    the Data Window happened to fall inside [btm, top] on some poll. Just a
    guess -- a close price can transit a range without any real wick-based
    retest occurring, or the read can be stale/wrong. Tagged "close".
  - Pine's own wick-based check (OBD_SecretTrader.pine's mark_retests(),
    exposed as the "Retested" 1/0 Data Window plots) -- authoritative,
    since it sees every bar's real high/low, not just whatever Close read
    at poll time. Recorded via mark(), tagged "pine".

Confirmed live (BTCUSD/M1): a zone's "close"-sourced retested_at stayed
stuck at a false-positive timestamp indefinitely, even though Pine's own
Retested plot for that exact zone read 0 (never retested) on every single
poll afterward -- the old "never downgrade" rule had no way to self-correct
a genuine close-approximation misfire. reconcile() fixes this by letting a
"close"-sourced entry be cleared when Pine's own authoritative signal
disagrees, while a "pine"-sourced entry is never touched this way (Pine's
own ob_retested array entry is a one-way flag internally -- see
mark_retests() -- so Pine disagreeing with its own earlier positive would
mean something else is wrong, not that the retest didn't happen).
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
        self._source: dict[str, str] = {}  # key -> "pine" | "close"
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str, direction: str, price_key: int) -> str:
        return f"{symbol}|{timeframe}|{direction}|{price_key}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            retested_at: dict[str, int] = {}
            source: dict[str, str] = {}
            for key, value in raw.items():
                if isinstance(value, dict):
                    retested_at[key] = int(value["at"])
                    source[key] = value.get("source", "pine")
                else:
                    # Old flat {key: timestamp} format, from before source
                    # tracking existed -- no way to know which path set it,
                    # so default to "pine" (never auto-cleared by
                    # reconcile()) rather than risk silently discarding a
                    # genuine historical retest just because its origin
                    # wasn't recorded.
                    retested_at[key] = int(value)
                    source[key] = "pine"
            self._retested_at = retested_at
            self._source = source
        # AttributeError covers the even-older format (a plain list of
        # keys, from before retest_at timestamps were tracked at all) --
        # confirmed live: this crashed the whole scraper on startup rather
        # than just resetting, since that format has no .items(). Any
        # schema mismatch here is safe to just start fresh from --
        # retests are re-derived live anyway.
        except (json.JSONDecodeError, OSError, ValueError, AttributeError,
                TypeError, KeyError):
            self._retested_at = {}
            self._source = {}

    def _save(self) -> None:
        out = {
            key: {"at": ts, "source": self._source.get(key, "pine")}
            for key, ts in self._retested_at.items()
        }
        self._path.write_text(json.dumps(out))

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
            self._source[key] = "close"
            self._save()
            return now
        return None

    def mark(self, symbol: str, timeframe: str, direction: str, price_key: int,
             hint: int | None = None) -> int:
        """Unconditionally records this zone as retested (used when Pine's
        own wick-based check reports true) and returns the retested_at
        timestamp -- the existing one if already recorded (never
        downgraded/overwritten), otherwise `hint` if given, else `now`.

        `hint` is a real timestamp reconstructed by scraper.py from Pine's
        RetestedBarsAgo Data Window plot -- lets a retest that happened
        before this scraper's own polling caught it (e.g. right after a
        restart, or during the 2-poll confirmation gate's own delay) get
        its true bar time instead of "whenever this module first noticed
        it." See OBD_SecretTrader.pine's own comment on why this is a bar
        count, not a raw timestamp.

        Always tags the source "pine", even if it's replacing/confirming a
        "close"-sourced value -- Pine's own check is strictly more
        authoritative, so once it has spoken for a zone, that zone's
        record should never again be eligible for reconcile()'s
        close-only downgrade."""
        key = self._key(symbol, timeframe, direction, price_key)
        existing = self._retested_at.get(key)
        if existing is not None:
            if self._source.get(key) != "pine":
                self._source[key] = "pine"
                self._save()
            return existing
        value = hint if hint is not None else int(time.time())
        self._retested_at[key] = value
        self._source[key] = "pine"
        self._save()
        return value

    def peek(self, symbol: str, timeframe: str, direction: str, price_key: int) -> Optional[int]:
        """Read-only lookup -- unlike check(), never records anything, just
        reports the currently-known retested_at (or None). Used by
        scraper._find_resurrectable() to source a value for
        RetestTracker.restore() without side effects."""
        key = self._key(symbol, timeframe, direction, price_key)
        return self._retested_at.get(key)

    def restore(self, symbol: str, timeframe: str, direction: str, price_key: int,
                retested_at: Optional[int], source: str = "pine") -> None:
        """Re-establishes a retested_at entry at an EXACT known value --
        used by scraper._find_resurrectable() alongside
        FirstSeenStore.restore() when a zone falsely read as mitigated
        (pure top-4 display churn) reappears, so its real retest history
        (from the ZoneStore entry being resurrected) isn't silently
        replaced by a fresh wall-clock guess on the very next poll.
        retested_at=None means the resurrected zone was never actually
        retested -- clears any stale entry rather than leaving one
        behind."""
        key = self._key(symbol, timeframe, direction, price_key)
        if retested_at is None:
            changed = False
            if key in self._retested_at:
                del self._retested_at[key]
                changed = True
            if key in self._source:
                del self._source[key]
                changed = True
            if changed:
                self._save()
            return
        if self._retested_at.get(key) != retested_at or self._source.get(key) != source:
            self._retested_at[key] = retested_at
            self._source[key] = source
            self._save()

    def reconcile(self, symbol: str, timeframe: str, direction: str, price_key: int,
                  pine_retested: bool) -> None:
        """Called whenever Pine's own Retested plot is present this poll,
        to let its authoritative signal correct a previously-recorded
        FALSE POSITIVE from this module's own live-Close approximation
        (check()). Confirmed live (BTCUSD/M1, zone continuously tracked
        since 12:49): a "close"-sourced retested_at stayed stuck even
        though Pine's own Retested plot read 0 on every poll afterward.

        pine_retested=False only ever clears a "close"-sourced entry,
        NEVER a "pine"-sourced one -- Pine's ob_retested array entry is a
        one-way flag internally (see OBD_SecretTrader.pine's
        mark_retests(): once set true for a slot, LuxAlgo's own code
        never resets it back to false except by fully removing that zone
        from the array on real mitigation), so a "pine"-sourced positive
        disagreeing with THIS poll's Pine read would mean something else
        is wrong (a scrape glitch, or a zone-identity mixup elsewhere,
        not that the retest didn't happen) -- silently discarding a real
        Pine-confirmed retest on that suspicion is a worse failure mode
        than leaving a rare inconsistency visible for a human to
        investigate.

        pine_retested=True needs no handling here -- mark() (called
        separately once the raw flag confirms it) is what records a
        positive; this method only ever removes, never adds."""
        if pine_retested:
            return
        key = self._key(symbol, timeframe, direction, price_key)
        if self._source.get(key) == "close" and key in self._retested_at:
            del self._retested_at[key]
            del self._source[key]
            self._save()

    def forget(self, symbol: str, timeframe: str, direction: str, price_key: int) -> None:
        """Call once a zone is confirmed mitigated -- see first_seen_store.py's
        forget() for why (a future zone at a coincidentally similar price
        must not inherit this one's retest status)."""
        key = self._key(symbol, timeframe, direction, price_key)
        changed = False
        if key in self._retested_at:
            del self._retested_at[key]
            changed = True
        if key in self._source:
            del self._source[key]
            changed = True
        if changed:
            self._save()
