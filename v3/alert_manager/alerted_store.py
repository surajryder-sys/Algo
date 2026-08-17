"""Persists which zones have already fired a Telegram alert, so a restart
doesn't re-send. Mirrors the old, now-deleted algo/alerts.py's
AlertedZoneStore pattern (see project_virgin_zone_telegram_alerts memory)
-- same idea, rebuilt fresh against tv_scraper's zone identity instead of
the old bot's own.

Keyed by the zone's own (symbol, timeframe, direction, start_time) --
that 4-tuple is already a stable, unique zone identity in tv_scraper's
ZoneStore (see v3/tradingview_bot/zone_store.py), so no separate
price-based key is needed here the way the old store needed one.
"""
from __future__ import annotations

import json
from pathlib import Path


class AlertedZoneStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._alerted: set[str] = set()
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str, direction: str, start_time: int) -> str:
        return f"{symbol}|{timeframe}|{direction}|{start_time}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._alerted = set(json.loads(self._path.read_text()))
        except (json.JSONDecodeError, OSError):
            self._alerted = set()

    def _save(self) -> None:
        self._path.write_text(json.dumps(sorted(self._alerted)))

    def already_alerted(self, symbol: str, timeframe: str, direction: str, start_time: int) -> bool:
        return self._key(symbol, timeframe, direction, start_time) in self._alerted

    def mark_alerted(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        key = self._key(symbol, timeframe, direction, start_time)
        if key not in self._alerted:
            self._alerted.add(key)
            self._save()

    def forget(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        """Call once a zone is gone from ZoneStore (mitigated/deleted) --
        frees the key so a genuinely new, different zone that later forms
        at a coincidentally identical start_time (vanishingly unlikely,
        but the same defensive habit as FirstSeenStore.forget()) doesn't
        inherit a stale alerted status."""
        key = self._key(symbol, timeframe, direction, start_time)
        if key in self._alerted:
            self._alerted.discard(key)
            self._save()
