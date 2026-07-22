"""Local JSON-backed persistence for traded zones, frozen detection snapshots,
and exposure metadata.

This plays the role the MQL5 EA fills with terminal Global Variables, without
depending on the MT5 terminal (or a chart) for storage, so it survives bot
restarts on its own.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_LOCK = threading.Lock()


class StateStore:
    def __init__(self, path: Path | None, in_memory: bool = False):
        """`in_memory=True` skips all disk I/O (used by the backtester so
        replay runs never touch the live bot's state file)."""
        self.path = path
        self.in_memory = in_memory
        self._data: dict[str, Any] = {
            "traded_zones": {},   # zone_hash -> traded_at (unix ts)
            "frozen": {},         # zone_hash -> {"time": ts, "price": float}
            "exposure": None,     # {"zone_hash", "direction", "source", "event_time", "tf_minutes"}
        }
        self._load()

    def _load(self) -> None:
        if not self.in_memory and self.path is not None and self.path.exists():
            try:
                self._data.update(json.loads(self.path.read_text()))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Could not read state file %s (%s); starting fresh.", self.path, exc)

    def _save(self) -> None:
        if self.in_memory or self.path is None:
            return
        with _LOCK:
            self.path.write_text(json.dumps(self._data, indent=2))

    def is_traded(self, zone_hash: str) -> bool:
        return zone_hash in self._data["traded_zones"]

    def mark_traded(self, zone_hash: str, when_ts: float) -> None:
        self._data["traded_zones"][zone_hash] = when_ts
        self._save()

    def clear_traded(self, zone_hash: str) -> None:
        if zone_hash in self._data["traded_zones"]:
            del self._data["traded_zones"][zone_hash]
            self._save()

    def get_frozen(self, zone_hash: str) -> tuple[float, float] | None:
        entry = self._data["frozen"].get(zone_hash)
        if not entry:
            return None
        return entry["time"], entry["price"]

    def set_frozen(self, zone_hash: str, when_ts: float, price: float) -> None:
        if zone_hash in self._data["frozen"]:
            return
        self._data["frozen"][zone_hash] = {"time": when_ts, "price": price}
        self._save()

    def get_exposure(self) -> dict | None:
        return self._data["exposure"]

    def set_exposure(self, meta: dict | None) -> None:
        self._data["exposure"] = meta
        self._save()
