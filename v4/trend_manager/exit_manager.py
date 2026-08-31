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
    """Persists PER-TICKET (a dict keyed by MT5 ticket id, one entry per
    currently/recently open position) peak favor and which one-time
    actions have already fired for that specific ticket.

    Rewritten 2026-08-31 from a single-ticket-scalar design -- confirmed
    live that "V4 only ever holds one position at a time" is FALSE (see
    broker.get_position's own docstring): a tier2 partial-close leaves a
    leftover ticket that keeps its own id, and a later same-direction
    fire opens an entirely separate new ticket alongside it, so TWO real
    tickets legitimately coexisted for hours. The old single-scalar
    design could only ever track one of them -- whichever ticket
    main.py's caller happened to look at -- so the other silently got
    NO breakeven/tier management at all. main.py now loops over EVERY
    open ticket (broker.get_all_positions) and calls this per-ticket.

    Old single-ticket schema ({"ticket": ..., "peak_favor": ...} with no
    per-ticket nesting) is migrated on load into the new dict keyed by
    that one ticket, so an in-flight state file isn't lost across this
    change."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._tickets: dict[int, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return

        if "tickets" in raw:
            # New schema: {"tickets": {"<ticket>": {...}, ...}}
            try:
                self._tickets = {int(k): v for k, v in raw["tickets"].items()}
            except (TypeError, ValueError, AttributeError):
                self._tickets = {}
        elif raw.get("ticket") is not None:
            # Old single-ticket schema -- migrate in place.
            ticket = raw["ticket"]
            self._tickets = {ticket: {
                "peak_favor": float(raw.get("peak_favor", 0.0)),
                "breakeven_applied": bool(raw.get("breakeven_applied", False)),
                "tier1_booked": bool(raw.get("tier1_booked", False)),
                "tier2_booked": bool(raw.get("tier2_booked", False)),
            }}

    def _save(self) -> None:
        self._path.write_text(json.dumps({"tickets": {str(k): v for k, v in self._tickets.items()}}, indent=2))

    def _entry(self, ticket: int) -> dict:
        entry = self._tickets.setdefault(ticket, {})
        entry.setdefault("peak_favor", 0.0)
        entry.setdefault("breakeven_applied", False)
        entry.setdefault("tier1_booked", False)
        entry.setdefault("tier2_booked", False)
        return entry

    def prune(self, open_tickets: set) -> None:
        """Drop tracking for any ticket no longer open -- called once per
        poll with the full set of currently open tickets, so closed
        positions don't accumulate in this file forever. Safe/idempotent
        if nothing needs pruning."""
        stale = [t for t in self._tickets if t not in open_tickets]
        if stale:
            for t in stale:
                del self._tickets[t]
            self._save()

    def update_peak_favor(self, ticket: int, favor: float) -> float:
        entry = self._entry(ticket)
        if favor > entry["peak_favor"]:
            entry["peak_favor"] = favor
            self._save()
        return entry["peak_favor"]

    def mark_breakeven_applied(self, ticket: int) -> None:
        self._entry(ticket)["breakeven_applied"] = True
        self._save()

    def mark_tier_booked(self, ticket: int, tier: Literal["tier1", "tier2"]) -> None:
        key = "tier1_booked" if tier == "tier1" else "tier2_booked"
        self._entry(ticket)[key] = True
        self._save()

    def breakeven_applied(self, ticket: int) -> bool:
        return self._entry(ticket)["breakeven_applied"]

    def tier1_booked(self, ticket: int) -> bool:
        return self._entry(ticket)["tier1_booked"]

    def tier2_booked(self, ticket: int) -> bool:
        return self._entry(ticket)["tier2_booked"]


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
    if peak >= BREAKEVEN_POINTS and not state.breakeven_applied(ticket):
        if _tighter(direction, entry_price, current_sl):
            sl_update = SLUpdate(new_sl=entry_price)
        state.mark_breakeven_applied(ticket)

    closes: list[PartialClose] = []
    if peak >= TIER1_POINTS and not state.tier1_booked(ticket):
        closes.append(PartialClose(tier="tier1", volume=round(fixed_lot_size * TIER1_FRACTION, 2)))
        state.mark_tier_booked(ticket, "tier1")
    if peak >= TIER2_POINTS and not state.tier2_booked(ticket):
        closes.append(PartialClose(tier="tier2", volume=round(fixed_lot_size * TIER2_FRACTION, 2)))
        state.mark_tier_booked(ticket, "tier2")

    return sl_update, closes
