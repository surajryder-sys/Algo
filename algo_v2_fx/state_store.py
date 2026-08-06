"""Persists which zone keys have already been traded, so a restart doesn't
re-trade the same OB. One shared file across all FX symbols -- zone_key
already embeds the symbol (see main.py's _zone_key), so a single JSON set is
enough; this bot deals with a handful of zones at a time, not a scale that
needs a real database. Identical to algo_v2/state_store.py.
"""
from __future__ import annotations

import json
from pathlib import Path


class TradedZoneStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._traded: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._traded = set(data.get("traded", []))
        except (json.JSONDecodeError, OSError):
            self._traded = set()

    def _save(self) -> None:
        self._path.write_text(json.dumps({"traded": sorted(self._traded)}))

    def is_traded(self, zone_key: str) -> bool:
        return zone_key in self._traded

    def mark_traded(self, zone_key: str) -> None:
        self._traded.add(zone_key)
        self._save()
