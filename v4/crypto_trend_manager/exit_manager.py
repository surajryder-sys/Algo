"""Crypto Exit/SL Manager -- BTCUSD/ETHUSD, own thresholds, own (more
active) mechanic than XAUUSD's exit_manager.py. Per the user's explicit
numbers (2026-08-29, BTCUSD's trail mechanic revised 2026-08-30):

  BTCUSD: breakeven +500pts, tier1 0.03 lot at +500pts, tier2 0.01 lot at
          +900pts, last 0.01 (of the 0.05 fixed lot) rides a CONTINUOUSLY
          TRAILING SL that maintains a 500pt gap behind price, updating
          every 300pts of further favor.
  ETHUSD: breakeven +24pts, tier1 0.60 lot at +35pts, tier2 0.20 lot at
          +50pts, last 0.20 (of the 1.0 fixed lot) rides a continuously
          trailing SL stepping by 25pts (gap == step, unchanged).

Two independent mechanisms running side by side, unlike XAUUSD's (which
freezes SL at breakeven permanently once triggered):
  - Tiered partial-booking: one-time-fire at each of tier1/tier2, keyed
    off PEAK favor (never un-fires on a pullback) -- identical in shape to
    XAUUSD's own exit_manager.py.
  - Continuous step-trailing: TRAIL_STEP is the GRANULARITY the trail
    updates at (peak favor advancing another full step is what triggers
    a re-check); TRAIL_GAP is the DISTANCE maintained behind the highest
    step level reached so far. These were the same number for both
    symbols originally (gap always == step, "one step behind"), until
    2026-08-30's explicit BTCUSD revision decoupled them: step stays 300
    (still updates every 300pts) but the maintained gap widened to 500
    ("maintain a gap of 500... trailing for every 300 points, maintaining
    a gap of 500 points"). ETHUSD's gap==step relationship is unchanged.
    Formula: SL = entry +/- (step*floor(peak/step) - gap), only once that
    offset is actually favorable -- see _trail_level's own docstring.
    Runs independently of, and after, breakeven/tier1/tier2, all the way
    to whenever the position eventually exits. Breakeven is tracked as
    its OWN candidate, separate from the trail formula's first level --
    necessary now more than ever for BTCUSD, since breakeven (500) and
    the trail formula (step=300, gap=500) don't coincide at any single
    peak value the way they used to when gap==step==300 (see
    evaluate_exit_actions's own comment for why both must be checked).

evaluate_exit_actions() always proposes SL = whichever of {current SL,
breakeven, trail-gap level} is more protective (see _tighter()) -- never
loosens, same defensive check used everywhere else in this repo for SL
management.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

Direction = Literal["buy", "sell"]

BREAKEVEN_POINTS = {"BTCUSD": 500.0, "ETHUSD": 24.0}
TIER1_POINTS = {"BTCUSD": 500.0, "ETHUSD": 35.0}
TIER2_POINTS = {"BTCUSD": 900.0, "ETHUSD": 50.0}
TRAIL_STEP = {"BTCUSD": 300.0, "ETHUSD": 25.0}
# Distance maintained behind the current step level -- equal to TRAIL_STEP
# for ETHUSD (unchanged "one step behind" behavior), decoupled to 500 for
# BTCUSD per the 2026-08-30 revision.
TRAIL_GAP = {"BTCUSD": 500.0, "ETHUSD": 25.0}

# Explicit volumes, not lot_size * fraction -- per the user's own numbers,
# 2026-08-29 ("just in case if not getting percentages"): BTCUSD 0.05 lot
# -> 0.03/0.01 (last 0.01 rides the trail/flip), ETHUSD 1.0 lot -> 0.6/0.2
# (last 0.2 rides it). These already match what 60%/20% of each symbol's
# own fixed lot size would compute to -- hardcoded anyway to remove any
# floating-point-multiplication risk entirely, not because the percentages
# were wrong.
TIER1_VOLUME = {"BTCUSD": 0.03, "ETHUSD": 0.60}
TIER2_VOLUME = {"BTCUSD": 0.01, "ETHUSD": 0.20}


@dataclass
class SLUpdate:
    new_sl: float


@dataclass
class PartialClose:
    tier: Literal["tier1", "tier2"]
    volume: float


class ExitManagerState:
    """Per-symbol-per-ticket tracking -- a fresh ticket (new position)
    resets all of it automatically, same as XAUUSD's own exit_manager.py."""

    def __init__(self, path: str):
        self._path = Path(path)
        # symbol -> {"ticket", "peak_favor", "tier1_booked", "tier2_booked"}
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


def _favor_points(direction: Direction, entry_price: float, current_price: float) -> float:
    return (current_price - entry_price) if direction == "buy" else (entry_price - current_price)


def _tighter(direction: Direction, candidate_sl: float, current_sl: float) -> bool:
    return candidate_sl > current_sl if direction == "buy" else candidate_sl < current_sl


def _trail_level(symbol: str, direction: Direction, entry_price: float, peak_favor: float) -> Optional[float]:
    """The trailing SL implied by the highest step level peak_favor has
    reached so far, held TRAIL_GAP behind that level -- e.g. BTCUSD
    (step=300, gap=500): peak 600 -> entry+100, peak 900 -> entry+400,
    peak 1200 -> entry+700. Where gap==step (ETHUSD, and BTCUSD's own
    original behavior before 2026-08-30), this reduces to exactly "one
    step behind": peak 300 -> entry+0, peak 600 -> entry+300, etc.
    None below the first full step, OR while the computed offset is still
    non-favorable (gap wider than the step level reached so far -- e.g.
    BTCUSD at peak=300, offset=300-500=-200) -- nothing to trail yet
    either way; breakeven (checked separately, see this module's own
    docstring) is what protects the position in that gap."""
    step = TRAIL_STEP[symbol]
    gap = TRAIL_GAP[symbol]
    if peak_favor < step:
        return None
    n = math.floor(peak_favor / step)
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

    # Two independent SL candidates -- breakeven (a single fixed level,
    # entry price) and the continuous step-trail (see _trail_level's own
    # docstring). ETHUSD's breakeven/step nearly coincide (24 vs 25);
    # BTCUSD's no longer do at all since the 2026-08-30 gap widening
    # (breakeven=500, but step=300/gap=500 means the trail formula's
    # offset stays negative -- non-favorable -- until peak reaches 900,
    # e.g. at peak=600 offset=600-500=100, favorable already, but at
    # peak=500 itself offset=300-500=-200, not yet). Either way,
    # breakeven must be checked as its OWN candidate, never assumed to be
    # subsumed by the trail formula's first level, or a symbol could wait
    # past its own explicit breakeven point before anything protects it.
    candidates = []
    if peak >= BREAKEVEN_POINTS[symbol]:
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
    if peak >= TIER1_POINTS[symbol] and not state.tier_booked(symbol, ticket, "tier1"):
        closes.append(PartialClose(tier="tier1", volume=TIER1_VOLUME[symbol]))
        state.mark_tier_booked(symbol, ticket, "tier1")
    if peak >= TIER2_POINTS[symbol] and not state.tier_booked(symbol, ticket, "tier2"):
        closes.append(PartialClose(tier="tier2", volume=TIER2_VOLUME[symbol]))
        state.mark_tier_booked(symbol, ticket, "tier2")

    return sl_update, closes
