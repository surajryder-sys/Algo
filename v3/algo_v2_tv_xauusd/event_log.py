"""Append-only history of every OB/ATR/bias event this bot observes, across
BOTH tv_bridge (alerts) and tv_scraper -- already merged by reader.py by the
time anything here sees it.

Why this exists separately from ZoneStore/AtrStore: those only ever hold
CURRENT state -- once a zone is mitigated or a bias flips, the previous
value is simply gone, overwritten on the next save. There is no way to
later ask "how long has M1 been WEAK" or "did M3's OB form before or after
M15's ATR flip" from current-state stores alone. This log is the durable
record those questions need -- v1 scope is purely observational (append
and print only, matching algo_v2_tv_xauusd's own "no trading yet" v1
scope); execution logic gets built on top of this later.

Every record carries TWO timestamps, deliberately kept distinct:
  event_time / detected_time / retested_at / mitigated_time -- the
    SOURCE's own knowledge of when the thing actually happened (a real
    Pine bar time via the alert path; tv_scraper's "first observed"
    approximation via the scraper path -- see retest_tracker.py's and
    first_seen_store.py's own docstrings for why those two can differ
    slightly from the true moment).
  recorded_at -- wall-clock time.time() when THIS process (not the
    source) first noticed the fact, i.e. when this exact log line was
    written. Always >= the source's own timestamp.
Both matter for later time-based logic: the source timestamp is "when it
truly happened," recorded_at is "when the bot could first have known."
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


class EventLog:
    def __init__(self, path: str):
        self._path = Path(path)

    def _append(self, record: dict) -> None:
        record.setdefault("recorded_at", time.time())
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def ob_formed(self, symbol: str, timeframe: str, direction: str, zone_key: int,
                  top: float, btm: float, detected_time: int, detected_price: float) -> None:
        self._append({
            "type": "ob_formed", "symbol": symbol, "timeframe": timeframe,
            "direction": direction, "zone_key": zone_key, "top": top, "btm": btm,
            "detected_time": detected_time, "detected_price": detected_price,
        })

    def ob_retested(self, symbol: str, timeframe: str, direction: str, zone_key: int,
                     retested_at: int) -> None:
        self._append({
            "type": "ob_retested", "symbol": symbol, "timeframe": timeframe,
            "direction": direction, "zone_key": zone_key, "retested_time": retested_at,
        })

    def ob_mitigated(self, symbol: str, timeframe: str, direction: str, zone_key: int) -> None:
        self._append({
            "type": "ob_mitigated", "symbol": symbol, "timeframe": timeframe,
            "direction": direction, "zone_key": zone_key,
        })

    def atr_flip(self, symbol: str, timeframe: str, trend: int, event_time: int) -> None:
        self._append({
            "type": "atr_flip", "symbol": symbol, "timeframe": timeframe,
            "trend": trend, "event_time": event_time,
        })

    def bias_changed(self, symbol: str, timeframe: str, state: str, event_time: int) -> None:
        self._append({
            "type": "bias_changed", "symbol": symbol, "timeframe": timeframe,
            "state": state, "event_time": event_time,
        })
