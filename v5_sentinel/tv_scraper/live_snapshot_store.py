"""Persists the RAW current Data Window snapshot per (symbol, timeframe) --
whatever's on screen THIS poll, full overwrite every time, zero lifecycle
interpretation (no start_time, no virgin/mitigated tracking, no history).

Exists specifically because ZoneStore/AtrStore/RetestTracker's whole job is
building an INTERPRETED history across polls (first-seen timestamps,
mitigation, retest-ever), and that interpretation is exactly where every
bug this session traced back to lives (top-4 display-cap churn, slot-index
retest misattribution, tie-broken bias, scrape-glitch false mitigation --
see tv_scraper/scraper.py's and algo_v2_tv_xauusd/zone.py's own comments).
None of that is wrong to track for building future execution logic, but
none of it can ever be guaranteed to match what a human looking at the
actual chart sees AT THIS MOMENT, because by definition it's reasoning
about the past. This store answers a different, much simpler question --
"what does the Data Window say right now" -- with nothing in between.

zones here are exactly parser.ParsedState.bull_zones/bear_zones as read
this poll: up to 4 per direction (Bull1-4/Bear1-4, matches the indicator's
own bull_ext_last/bear_ext_last), each {"top", "btm", "retested"} (retested
omitted if the indicator's Retested plot wasn't present on this poll).
"""
from __future__ import annotations

import json
from pathlib import Path


class LiveSnapshotStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._snapshots: dict[str, dict] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str) -> str:
        return f"{symbol}|{timeframe}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._snapshots = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            self._snapshots = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._snapshots))

    def apply(self, symbol: str, timeframe: str, close, atr: dict | None,
              bull_zones: list[dict], bear_zones: list[dict], now: float) -> None:
        self._snapshots[self._key(symbol, timeframe)] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "close": close,
            "atr": atr,
            "bull": bull_zones,
            "bear": bear_zones,
            "updated_at": now,
        }
        self._save()

    def reload(self) -> None:
        """For readers in a different, long-running process -- same
        stale-forever-at-startup bug/fix as ZoneStore.reload()."""
        self._snapshots = {}
        self._load()

    def get(self, symbol: str, timeframe: str) -> dict | None:
        return self._snapshots.get(self._key(symbol, timeframe))
