"""SL Manager for Trend Manager's ICT component -- a THREE-stage
progression, distinct enough from sl_manager.SLManager's own two-stage
(pre-breakeven frozen / breakeven-then-far-line) model that it gets its
own class rather than a reused/parameterized one. Confirmed with the
user 2026-09-14 (final round of TM-ICT's own Q&A):

  Stage 1 -- OB-based, frozen. From entry until structure confirms in
  the trade's own favor: SL stays exactly at the initial OB-edge value
  (ict_ob_block.initial_sl()) -- no live movement at all. "sl for ict is
  ob low with buffer" (bullish) / "ob high with buffer" (bearish).

  Stage 2 -- structure-confirmed, far-line, NO breakeven floor yet. The
  moment M3's own ATR-dual CONFIRMED direction (bridge_bar_flip.
  BridgeBarFlipTracker, the SAME bar-close-gated state every other M3
  signal in this project uses) agrees with the position's own direction
  -- "waits for the price to move above atr trailing lines, if already
  above trailing lines that is cool, now initial sl is based on far
  trailing line with buffer" -- SL switches basis to M3's own LIVE far
  trail line +/- buffer, tightening-only, but does NOT yet floor at
  breakeven (that's a separate, later stage -- the user's own two
  answers were given as two distinct events, not one).

  Stage 3 -- partial-booked, breakeven-floored far-line. "breakeven sl
  once the partial booking is done, as per other components" -- once
  Trade Manager's own partial-booking has fired (TradeManager.
  is_partially_cut()), SL follows the SAME breakeven-then-far-line
  formula sl_manager.SLManager already uses for every other component
  (max/min of far-line-based and entry_price, tightening-only). Stage 3
  can arrive before OR after Stage 2 (partial booking is a points-in-
  favor trigger, independent of the structure-confirm timing) -- once
  it has, it always wins over Stage 2's own no-floor behavior.

Manual-edit pause/resume mechanics (a real broker-side SL change pauses
auto-trailing until cleared entirely, then resumes fresh off whatever
stage currently applies) are copied from sl_manager.SLManager verbatim,
same tolerances -- reusing its own module-level helpers rather than
duplicating the float-comparison logic.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from v5_sentinel.sl_manager import _MANUAL_CHANGE_TOLERANCE, _MIN_SL_IMPROVEMENT, _differs


@dataclass
class _ICTPositionSLState:
    entry_price: float
    last_bot_sl: Optional[float]
    # Captured ONCE at first sighting (whichever SL the broker already
    # shows then -- the true OB-based initial SL for a fresh entry, or
    # whatever basis was already legitimately active for a position this
    # process is only just now seeing after a restart) -- this is what a
    # later manual-clear-while-still-in-Stage-1 resumes to, with NO
    # dependency on this process still having the original zone reference
    # around (it may not, post-restart).
    initial_sl: float
    override_active: bool = False


class ICTSLManager:
    def __init__(self, path: str, sl_buffer: float):
        self._path = Path(path)
        self._sl_buffer = sl_buffer
        self._state: dict[int, _ICTPositionSLState] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for ticket_str, raw in data.get("positions", {}).items():
                self._state[int(ticket_str)] = _ICTPositionSLState(**raw)
        except (json.JSONDecodeError, OSError, TypeError):
            self._state = {}

    def _save(self) -> None:
        payload = {"positions": {str(t): asdict(s) for t, s in self._state.items()}}
        self._path.write_text(json.dumps(payload))

    def prune(self, open_tickets: set) -> None:
        stale = [t for t in self._state if t not in open_tickets]
        if stale:
            for t in stale:
                del self._state[t]
            self._save()

    def _far_side(self, direction: int, far_line: float) -> float:
        return far_line - self._sl_buffer if direction == 1 else far_line + self._sl_buffer

    def compute(self, ticket: int, direction: int, entry_price: float, current_broker_sl: Optional[float],
               initial_sl: float, far_line: Optional[float], structure_confirmed: bool,
               partial_booked: bool) -> Optional[float]:
        """Returns a new SL to apply this cycle, or None if nothing
        should change. Does NOT assume the caller applied it -- call
        confirm_applied() only after the broker call succeeds, same
        contract as sl_manager.SLManager.compute(). far_line may be None
        (M3 bridge stale) -- Stage 1 doesn't need it at all, and Stage
        2/3 simply skip this cycle's update (no guess) if it's missing
        while one of them would otherwise apply."""
        state = self._state.get(ticket)

        if state is None:
            baseline_sl = current_broker_sl if current_broker_sl is not None else initial_sl
            self._state[ticket] = _ICTPositionSLState(entry_price=entry_price, last_bot_sl=current_broker_sl,
                                                       initial_sl=baseline_sl)
            self._save()
            return None

        if current_broker_sl is None:
            if state.override_active:
                print(f"[V5S-TM-ICT-SL] #{ticket} SL manually cleared -- resuming auto control")
            state.override_active = False
            state.last_bot_sl = None
        elif _differs(current_broker_sl, state.last_bot_sl, _MANUAL_CHANGE_TOLERANCE):
            if not state.override_active:
                print(f"[V5S-TM-ICT-SL] #{ticket} manual SL change detected "
                      f"({state.last_bot_sl} -> {current_broker_sl}) -- auto control paused until cleared entirely")
                state.override_active = True
            state.last_bot_sl = current_broker_sl
            self._save()
            return None

        if state.override_active:
            self._save()
            return None

        if current_broker_sl is None:
            # Resume signal -- re-establish whichever stage currently
            # applies fresh, rather than leaving the position bare.
            if partial_booked and far_line is not None:
                proposed = max(self._far_side(direction, far_line), entry_price) if direction == 1 else \
                          min(self._far_side(direction, far_line), entry_price)
            elif structure_confirmed and far_line is not None:
                proposed = self._far_side(direction, far_line)
            else:
                proposed = state.initial_sl
            self._save()
            return proposed

        if not partial_booked and not structure_confirmed:
            # Stage 1 -- stays exactly where it was set at entry.
            self._save()
            return None

        if far_line is None:
            # Stage 2/3 would apply but M3's bridge has nothing right
            # now -- skip this cycle rather than guess, same contract
            # every other far-line consumer in this project follows.
            self._save()
            return None

        if partial_booked:
            far_side = self._far_side(direction, far_line)
            proposed = max(far_side, entry_price) if direction == 1 else min(far_side, entry_price)
        else:
            proposed = self._far_side(direction, far_line)  # Stage 2 -- no breakeven floor yet

        # current_broker_sl is guaranteed not None here -- the None case
        # already returned above (the "resume" branch).
        if direction == 1 and proposed <= current_broker_sl + _MIN_SL_IMPROVEMENT:
            self._save()
            return None
        if direction == -1 and proposed >= current_broker_sl - _MIN_SL_IMPROVEMENT:
            self._save()
            return None

        self._save()
        return proposed

    def confirm_applied(self, ticket: int, new_sl: float) -> None:
        state = self._state.get(ticket)
        if state is not None:
            state.last_bot_sl = new_sl
            self._save()
