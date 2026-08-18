"""Persisted state for stoploss_manager.py -- one entry per symbol,
reset whenever the tracked position changes to a different trade
(different exec_timeframe/exec_start_time), so a new trade always
starts its own trailing history fresh.

peak_favor_points: the highest favorable price movement (in raw price
units -- "points" per the user's own term, XAUUSD-scaled) ever observed
for the CURRENT position. Desired SL is computed from this PEAK, not
instantaneous profit -- a proper trailing stop only ever ratchets
tighter, never loosens if price gives back some of its favorable move.

last_managed_sl: what Stoploss Manager itself last set as the real SL,
used to detect a manual change (the real SL on the position no longer
matches what we last set -> the user moved it).

manual_override_active / override_price_reference: once a manual change
is detected, Stoploss Manager stops touching this position's SL until
price makes a genuinely NEW high (buy) / new low (sell) beyond the
price level at the moment the override was detected -- "dont change
until next new high for the buy trade, and vice versa for sell trade"
(user, 2026-08-18).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SymbolSLState:
    exec_timeframe: str
    exec_start_time: int
    peak_favor_points: float = 0.0
    last_managed_sl: Optional[float] = None
    manual_override_active: bool = False
    override_price_reference: Optional[float] = None


class SLStateStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[str, SymbolSLState] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._state = {symbol: SymbolSLState(**s) for symbol, s in raw.items()}

    def _save(self) -> None:
        self._path.write_text(json.dumps({symbol: asdict(s) for symbol, s in self._state.items()}))

    def get_or_reset(self, symbol: str, exec_timeframe: str, exec_start_time: int) -> SymbolSLState:
        """Returns this symbol's state, resetting it fresh if the
        tracked position has changed to a different trade since last
        seen (new exec_timeframe/exec_start_time)."""
        existing = self._state.get(symbol)
        if existing is not None and (existing.exec_timeframe, existing.exec_start_time) == (exec_timeframe, exec_start_time):
            return existing
        fresh = SymbolSLState(exec_timeframe=exec_timeframe, exec_start_time=exec_start_time)
        self._state[symbol] = fresh
        self._save()
        return fresh

    def clear(self, symbol: str) -> None:
        self._state.pop(symbol, None)
        self._save()

    def save(self) -> None:
        """Call after mutating a SymbolSLState object returned by
        get_or_reset -- dataclasses are mutable, mutated in place."""
        self._save()
