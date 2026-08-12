"""Tracks the tv_bridge signal log offset this bot has already processed (so
a restart doesn't replay old signals), plus the full history of what it has
seen so far. Plain JSON file -- same approach as the other bots' state_store.py.
"""
from __future__ import annotations

import json
from pathlib import Path


class SignalStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._cursor = 0
        self._history: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._cursor = data.get("cursor", 0)
            self._history = data.get("history", [])
        except (json.JSONDecodeError, OSError):
            self._cursor = 0
            self._history = []

    def _save(self) -> None:
        self._path.write_text(json.dumps({"cursor": self._cursor, "history": self._history}))

    @property
    def cursor(self) -> int:
        return self._cursor

    def record(self, cursor: int, signals: list[dict]) -> None:
        self._cursor = cursor
        self._history.extend(signals)
        self._save()
