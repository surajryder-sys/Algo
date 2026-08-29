"""V4 Exit/SL Manager -- profit-booking and breakeven for a currently
open position, per the user's explicit rule 2026-08-28 (adapted from
v3/execution_bridge's own stoploss_manager.py/exit_manager.py pattern,
same "lives separately from entry logic, needs a REAL open position's
real entry price/ticket" reasoning, but simpler numbers specific to V4):

  - Below 7 points favorable move: SL stays exactly wherever the entry
    logic set it (m1_execution.py's ATR-line-based initial SL) --
    completely untouched. This module does nothing at all below 7
    points -- "no initial sl comes with trade as per trailing stop, but
    once trade goes in favour, then sl manager manages it."
  - >= 7 points favor: SL moves to breakeven (entry price) -- but only
    if that's actually tighter than wherever the SL already sits (never
    loosens), same defensive check v3's own Stoploss Manager uses.
  - >= 10 points favor: books 60% of the FIXED lot size (0.03 of 0.05),
    once, ever, for this position.
  - >= 15 points favor: books another 20% of the FIXED lot size (0.01 of
    0.05), once, ever, for this position.
  - The remaining 20% is deliberately NOT booked by this module at any
    further threshold -- "last 20% for an auto square off" -- it just
    rides on whatever SL is currently set (breakeven, from the >=7pt
    rule above) until either that SL is hit or the existing
    opposite-signal reversal logic closes the position via netting.
    Nothing extra needed here for that leg.
  - All thresholds are evaluated against the PEAK favorable move ever
    reached for this position, not the instantaneous current one -- same
    "a real trailing/booking system only ever ratchets tighter, never
    reverses on a pullback" reasoning v3 uses. A single poll that jumps
    past more than one threshold at once (a real price gap) still fires
    every threshold it crossed, not just the first unfired one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

Direction = Literal["buy", "sell"]

BREAKEVEN_POINTS = 7.0
TIER1_POINTS = 10.0
TIER1_FRACTION = 0.6
TIER2_POINTS = 15.0
TIER2_FRACTION = 0.2


@dataclass
class SLUpdate:
    new_sl: float


@dataclass
class PartialClose:
    tier: Literal["tier1", "tier2"]
    volume: float


class ExitManagerState:
    """Persists per-position (keyed by MT5 ticket, since a fresh position
    needs fresh tracking) peak favor and which one-time actions have
    already fired. A ticket different from the tracked one means a new
    position -- state resets automatically rather than needing an
    explicit "position closed" signal, since main.py only ever calls
    this module when a real position currently exists."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._ticket: Optional[int] = None
        self._peak_favor: float = 0.0
        self._breakeven_applied: bool = False
        self._tier1_booked: bool = False
        self._tier2_booked: bool = False
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._ticket = raw.get("ticket")
            self._peak_favor = float(raw.get("peak_favor", 0.0))
            self._breakeven_applied = bool(raw.get("breakeven_applied", False))
            self._tier1_booked = bool(raw.get("tier1_booked", False))
            self._tier2_booked = bool(raw.get("tier2_booked", False))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    def _save(self) -> None:
        self._path.write_text(json.dumps({
            "ticket": self._ticket,
            "peak_favor": self._peak_favor,
            "breakeven_applied": self._breakeven_applied,
            "tier1_booked": self._tier1_booked,
            "tier2_booked": self._tier2_booked,
        }))

    def _ensure_ticket(self, ticket: int) -> None:
        if ticket != self._ticket:
            self._ticket = ticket
            self._peak_favor = 0.0
            self._breakeven_applied = False
            self._tier1_booked = False
            self._tier2_booked = False
            self._save()

    def update_peak_favor(self, ticket: int, favor: float) -> float:
        self._ensure_ticket(ticket)
        if favor > self._peak_favor:
            self._peak_favor = favor
            self._save()
        return self._peak_favor

    def mark_breakeven_applied(self) -> None:
        self._breakeven_applied = True
        self._save()

    def mark_tier_booked(self, tier: Literal["tier1", "tier2"]) -> None:
        if tier == "tier1":
            self._tier1_booked = True
        else:
            self._tier2_booked = True
        self._save()

    @property
    def breakeven_applied(self) -> bool:
        return self._breakeven_applied

    @property
    def tier1_booked(self) -> bool:
        return self._tier1_booked

    @property
    def tier2_booked(self) -> bool:
        return self._tier2_booked


def _favor_points(direction: Direction, entry_price: float, current_price: float) -> float:
    return (current_price - entry_price) if direction == "buy" else (entry_price - current_price)


def _tighter(direction: Direction, candidate_sl: float, current_sl: float) -> bool:
    """True if candidate_sl is actually more favorable (tighter) than
    current_sl -- never loosens, same check v3's Stoploss Manager uses."""
    return candidate_sl > current_sl if direction == "buy" else candidate_sl < current_sl


def evaluate_exit_actions(
    state: ExitManagerState,
    ticket: int,
    direction: Direction,
    entry_price: float,
    current_price: float,
    current_sl: float,
    fixed_lot_size: float,
) -> tuple[Optional[SLUpdate], list[PartialClose]]:
    """Call once per poll with the currently open position's real details.
    Returns (sl_update_or_None, list_of_partial_closes) -- both empty/None
    on a poll where nothing new has been reached. A single poll can return
    a partial close for BOTH tiers at once if a price gap jumped past
    both thresholds in one move."""
    favor = _favor_points(direction, entry_price, current_price)
    peak = state.update_peak_favor(ticket, favor)

    sl_update: Optional[SLUpdate] = None
    if peak >= BREAKEVEN_POINTS and not state.breakeven_applied:
        if _tighter(direction, entry_price, current_sl):
            sl_update = SLUpdate(new_sl=entry_price)
        state.mark_breakeven_applied()

    closes: list[PartialClose] = []
    if peak >= TIER1_POINTS and not state.tier1_booked:
        closes.append(PartialClose(tier="tier1", volume=round(fixed_lot_size * TIER1_FRACTION, 2)))
        state.mark_tier_booked("tier1")
    if peak >= TIER2_POINTS and not state.tier2_booked:
        closes.append(PartialClose(tier="tier2", volume=round(fixed_lot_size * TIER2_FRACTION, 2)))
        state.mark_tier_booked("tier2")

    return sl_update, closes
