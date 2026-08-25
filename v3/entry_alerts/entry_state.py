"""Persists which MT5 position tickets have already been alerted --
simpler than profit_alerts's ProfitState (one boolean per ticket, not a
set of milestones) since this only ever fires once per position, the
first time it's seen open. Ticket is unique and never reused, so this
alone is enough identity -- a restart doesn't re-fire an already-seen
entry.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Set


class EntryState:
    def __init__(self, path: str):
        self._path = Path(path)
        self._alerted: Set[int] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._alerted = set(int(t) for t in raw.get("alerted_tickets", []))

    def _save(self) -> None:
        self._path.write_text(json.dumps({"alerted_tickets": sorted(self._alerted)}))

    def already_alerted(self, ticket: int) -> bool:
        return ticket in self._alerted

    def mark_alerted(self, ticket: int) -> None:
        self._alerted.add(ticket)
        self._save()

    def prune(self, open_tickets: Set[int]) -> None:
        """Drops tracking for any ticket no longer among currently open
        positions -- keeps the state file from growing forever."""
        stale = self._alerted - open_tickets
        if not stale:
            return
        self._alerted -= stale
        self._save()
