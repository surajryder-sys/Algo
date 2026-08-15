"""Currently-live OB zones only -- a companion to EventLog, not a
replacement for it. EventLog is deliberately append-only (the durable "what
ever happened" record); this store is the opposite: exactly one entry per
zone that's CURRENTLY plotted on the chart, added the moment EventTracker
sees it form and REMOVED the moment EventTracker sees it mitigated (i.e.
disappear from what tv_scraper reads off the Data Window -- see
scraper.py's own missing-streak debounce for what "mitigated" means here:
gone from the visible zones for _MITIGATION_DEBOUNCE_POLLS consecutive
polls, matching "what's on chart visually" by explicit request, not a
separate invalidation check).

Exists so the future algo (or anything else) can read "what's live right
now" directly off this file's current contents, instead of re-deriving it
every time from reader.read_zone()'s full merged history (which keeps
every zone ever seen, mitigated or not -- see that module's own docstring)
and manually filtering out anything with mitigated_time set.
"""
from __future__ import annotations

import json
from pathlib import Path


class ActiveEventStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._active: dict[str, dict] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str, direction: str, start_time: int) -> str:
        return f"{symbol}|{timeframe}|{direction}|{start_time}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._active = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            self._active = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._active))

    def add(self, symbol: str, timeframe: str, direction: str, start_time: int,
            top: float, btm: float, detected_time: int, detected_price: float,
            retested_at: int | None = None) -> None:
        """Called on ob_formed. Idempotent -- a duplicate add (shouldn't
        happen, EventTracker only calls this once per zone via its own
        prev-is-None check) just overwrites with the same data.

        retested_at defaults to None (freshly formed, still virgin), but
        EventTracker passes the zone's actual value on startup backfill --
        a zone that was ALREADY retested (not just already mitigated) the
        first time this process ever saw it must not be recorded as virgin
        here, since the only other place retested_at gets set
        (mark_retested()) only fires on a live not-retested -> retested
        TRANSITION, which a zone retested before this process started
        watching will never produce."""
        key = self._key(symbol, timeframe, direction, start_time)
        self._active[key] = {
            "symbol": symbol, "timeframe": timeframe, "direction": direction,
            "start_time": start_time, "top": top, "btm": btm,
            "detected_time": detected_time, "detected_price": detected_price,
            "retested_at": retested_at,
        }
        self._save()

    def mark_retested(self, symbol: str, timeframe: str, direction: str,
                       start_time: int, retested_at: int) -> None:
        """Called on ob_retested. A zone stays in this store when retested
        -- retest is not mitigation (see retest_tracker.py's own comment on
        that distinction) -- this just updates the field so a reader can
        tell virgin from touched without going back to EventLog."""
        key = self._key(symbol, timeframe, direction, start_time)
        entry = self._active.get(key)
        if entry is None:
            return  # mitigated (or never added) before this retest landed -- ignore
        entry["retested_at"] = retested_at
        self._save()

    def remove(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        """Called on ob_mitigated -- the zone is gone from what's plotted,
        so it's gone from this store too. EventLog keeps the permanent
        record; this store only ever reflects the current chart."""
        key = self._key(symbol, timeframe, direction, start_time)
        if self._active.pop(key, None) is not None:
            self._save()

    def snapshot(self) -> dict[str, dict]:
        """Everything currently live, keyed as _key() produces -- for a
        reader that wants the whole current picture in one call."""
        return dict(self._active)
