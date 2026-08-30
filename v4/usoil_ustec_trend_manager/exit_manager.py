"""USOIL/USTEC Exit/SL Manager -- own thresholds, same shape as
crypto_trend_manager's own exit_manager.py (breakeven + tiered partial-
booking + a continuously trailing SL). Per the user's explicit numbers,
2026-08-31:

  USOIL: breakeven +0.600pts (reused from the old, proven v3
         execution_bridge value), tier1 0.03 lot at +1.5pts, tier2
         0.01 lot at +2.5pts, last 0.01 (of the 0.05 fixed lot) rides a
         continuously trailing SL stepping by 0.600pts (gap == step,
         reused unchanged from the old value -- USOIL's breakeven/
         trail_start/trail_step were all the same number, 0.600, in the
         old design too).
  USTEC: breakeven +150pts (reused), tier1 0.15 lot at +150pts
         (coincides with breakeven), tier2 0.05 lot at +200pts, last
         0.05 (of the 0.25 fixed lot) rides a continuously trailing SL
         stepping by 100pts (reused from the old trail_step value,
         which differed slightly from breakeven there too -- same
         "gap==step but not ==breakeven" shape as ETHUSD).

Tier volumes are hardcoded, not lot_size*fraction -- same reasoning as
crypto_trend_manager's own TIER1_VOLUME/TIER2_VOLUME (remove any
floating-point-multiplication risk).

See crypto_trend_manager/exit_manager.py's own docstring for the full
mechanics (breakeven vs. continuous step-trail as two independent SL
candidates, PEAK-favor-based one-time tier booking, never-loosens SL).
This module reuses that exact logic, own copy per this repo's usual
per-bot isolation convention.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

Direction = Literal["buy", "sell"]

BREAKEVEN_POINTS = {"USOIL": 0.600, "USTEC": 150.0}
TIER1_POINTS = {"USOIL": 1.5, "USTEC": 150.0}
TIER2_POINTS = {"USOIL": 2.5, "USTEC": 200.0}
TRAIL_STEP = {"USOIL": 0.600, "USTEC": 100.0}
TRAIL_GAP = {"USOIL": 0.600, "USTEC": 100.0}  # gap == step for both, unlike BTCUSD's decoupled 500/300

TIER1_VOLUME = {"USOIL": 0.03, "USTEC": 0.15}
TIER2_VOLUME = {"USOIL": 0.01, "USTEC": 0.05}


@dataclass
class SLUpdate:
    new_sl: float


@dataclass
class PartialClose:
    tier: Literal["tier1", "tier2"]
    volume: float


class ExitManagerState:
    """Per-symbol-per-ticket tracking -- a fresh ticket (new position)
    resets all of it automatically."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2))

    def _entry(self, symbol: str, ticket: int) -> dict:
        e = self._data.get(symbol)
        if e is None or e.get("ticket") != ticket:
            e = {"ticket": ticket, "peak_favor": 0.0, "tier1_booked": False, "tier2_booked": False}
            self._data[symbol] = e
            self._save()
        return e

    def update_peak_favor(self, symbol: str, ticket: int, favor: float) -> float:
        e = self._entry(symbol, ticket)
        if favor > e["peak_favor"]:
            e["peak_favor"] = favor
            self._save()
        return e["peak_favor"]

    def tier_booked(self, symbol: str, ticket: int, tier: Literal["tier1", "tier2"]) -> bool:
        return self._entry(symbol, ticket)[f"{tier}_booked"]

    def mark_tier_booked(self, symbol: str, ticket: int, tier: Literal["tier1", "tier2"]) -> None:
        self._entry(symbol, ticket)[f"{tier}_booked"] = True
        self._save()


# Confirmed live, 2026-08-31: entry=71.500 + favor=0.600 computes to
# favor=0.5999999999999943 in real float64 arithmetic (subtracting back
# through entry_price re-introduces its own rounding error) -- a bare
# `favor >= threshold` then wrongly fails right at the boundary. USOIL's
# thresholds are sub-1-point (0.6, 1.5, 2.5), the highest-risk case in
# this repo for exactly this kind of float precision gap -- USTEC/crypto's
# larger round-number thresholds are far less exposed to it but not
# theoretically immune either. A tolerance far smaller than any real
# price tick absorbs the rounding error without ever letting a genuinely
# short move count as reaching the threshold.
_EPSILON = 1e-6


def _favor_points(direction: Direction, entry_price: float, current_price: float) -> float:
    return (current_price - entry_price) if direction == "buy" else (entry_price - current_price)


def _tighter(direction: Direction, candidate_sl: float, current_sl: float) -> bool:
    return candidate_sl > current_sl if direction == "buy" else candidate_sl < current_sl


def _trail_level(symbol: str, direction: Direction, entry_price: float, peak_favor: float) -> Optional[float]:
    step = TRAIL_STEP[symbol]
    gap = TRAIL_GAP[symbol]
    if peak_favor < step - _EPSILON:
        return None
    n = math.floor((peak_favor + _EPSILON) / step)
    offset = step * n - gap
    if offset < 0:
        return None
    return entry_price + offset if direction == "buy" else entry_price - offset


def evaluate_exit_actions(
    state: ExitManagerState,
    symbol: str,
    ticket: int,
    direction: Direction,
    entry_price: float,
    current_price: float,
    current_sl: float,
) -> tuple[Optional[SLUpdate], list[PartialClose]]:
    favor = _favor_points(direction, entry_price, current_price)
    peak = state.update_peak_favor(symbol, ticket, favor)

    candidates = []
    if peak >= BREAKEVEN_POINTS[symbol] - _EPSILON:
        candidates.append(entry_price)
    trail_level = _trail_level(symbol, direction, entry_price, peak)
    if trail_level is not None:
        candidates.append(trail_level)

    sl_update: Optional[SLUpdate] = None
    if candidates:
        best = max(candidates) if direction == "buy" else min(candidates)
        if _tighter(direction, best, current_sl):
            sl_update = SLUpdate(new_sl=best)

    closes: list[PartialClose] = []
    if peak >= TIER1_POINTS[symbol] - _EPSILON and not state.tier_booked(symbol, ticket, "tier1"):
        closes.append(PartialClose(tier="tier1", volume=TIER1_VOLUME[symbol]))
        state.mark_tier_booked(symbol, ticket, "tier1")
    if peak >= TIER2_POINTS[symbol] - _EPSILON and not state.tier_booked(symbol, ticket, "tier2"):
        closes.append(PartialClose(tier="tier2", volume=TIER2_VOLUME[symbol]))
        state.mark_tier_booked(symbol, ticket, "tier2")

    return sl_update, closes
