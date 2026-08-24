"""Persists which profit milestones have already been alerted for each
open position, keyed by MT5's own position ticket (unique, never
reused) -- so a restart doesn't re-fire an already-sent milestone, and
a position that closes and a later, unrelated position that happens to
reuse similar price levels never gets confused with it (ticket, not
price, is the identity here).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Set


class ProfitState:
    def __init__(self, path: str):
        self._path = Path(path)
        self._alerted: dict[int, Set[float]] = {}  # ticket -> {milestones already alerted}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._alerted = {int(ticket): set(milestones) for ticket, milestones in raw.items()}

    def _save(self) -> None:
        out = {str(ticket): sorted(milestones) for ticket, milestones in self._alerted.items()}
        self._path.write_text(json.dumps(out))

    def already_alerted(self, ticket: int, milestone: float) -> bool:
        return milestone in self._alerted.get(ticket, set())

    def mark_alerted(self, ticket: int, milestone: float) -> None:
        self._alerted.setdefault(ticket, set()).add(milestone)
        self._save()

    def prune(self, open_tickets: Set[int]) -> None:
        """Drops tracking for any ticket no longer among currently open
        positions -- called once per poll with the full live set, so
        closed/filled-and-closed positions don't accumulate forever."""
        stale = set(self._alerted.keys()) - open_tickets
        if not stale:
            return
        for ticket in stale:
            del self._alerted[ticket]
        self._save()
