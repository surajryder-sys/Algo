"""Tracks the tv_bridge signal log offset this bot has already processed
(so a restart doesn't replay old signals). Plain JSON file -- same approach
as the other bots' state_store.py.

Used to also carry a "history" list of every raw event ever seen, growing
forever. Dropped 2026-08-22 -- nothing in the codebase ever read it (verified
by grep), it was pure unbounded storage for no consumer, and it's the exact
opposite of the user's explicit "don't keep data past the point it's still
needed" instruction. ZoneStore/AtrStore are the actual state anything reads;
this file's only job is the cursor.
"""
from __future__ import annotations

import json
from pathlib import Path


class SignalStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._cursor = 0
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._cursor = data.get("cursor", 0)
        except (json.JSONDecodeError, OSError):
            self._cursor = 0

    def _save(self) -> None:
        self._path.write_text(json.dumps({"cursor": self._cursor}))

    @property
    def cursor(self) -> int:
        return self._cursor

    def record(self, cursor: int) -> None:
        self._cursor = cursor
        self._save()
