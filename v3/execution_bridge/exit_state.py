"""Persisted state for exit_manager.py -- one entry per symbol, reset
whenever the tracked position changes to a different trade (different
exec_timeframe/exec_start_time), same convention as sl_state.py's own
SLStateStore.

booked_tier_points: which of this symbol's own SymbolConfig.
partial_tiers have already fired for the CURRENT position, keyed by
each tier's own points value (not a plain index -- config.py's own
tuple is the single source of truth for what a tier actually IS; this
just remembers "already done" so a tier never double-books across
polls). A float key stored as its repr-stable string form via JSON
(json.dumps/loads round-trips floats fine as dict keys once cast to
str) -- see get_or_reset/save below.

breakeven_applied: whether Exit Manager has already moved this
position's SL to breakeven once (its own one-time move, independent of
whatever Stoploss Manager's own trailing separately decides) -- set the
moment the FIRST tier of this position ever books, never touched again
after that.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List


@dataclass
class SymbolExitState:
    exec_timeframe: str
    exec_start_time: int
    booked_tier_points: List[float] = field(default_factory=list)
    breakeven_applied: bool = False


class ExitStateStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[str, SymbolExitState] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._state = {symbol: SymbolExitState(**s) for symbol, s in raw.items()}

    def _save(self) -> None:
        self._path.write_text(json.dumps({symbol: asdict(s) for symbol, s in self._state.items()}))

    def get_or_reset(self, symbol: str, exec_timeframe: str, exec_start_time: int) -> SymbolExitState:
        """Same "reset fresh on a new trade" reasoning as SLStateStore's
        own get_or_reset -- a new position always starts its own
        partial-booking history from scratch."""
        existing = self._state.get(symbol)
        if existing is not None and (existing.exec_timeframe, existing.exec_start_time) == (exec_timeframe, exec_start_time):
            return existing
        fresh = SymbolExitState(exec_timeframe=exec_timeframe, exec_start_time=exec_start_time)
        self._state[symbol] = fresh
        self._save()
        return fresh

    def clear(self, symbol: str) -> None:
        self._state.pop(symbol, None)
        self._save()

    def save(self) -> None:
        """Call after mutating a SymbolExitState object returned by
        get_or_reset -- dataclasses are mutable, mutated in place."""
        self._save()
