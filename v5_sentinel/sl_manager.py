"""SL Manager: breakeven-then-far-trail-line trailing stop, per open
position. Persisted to disk (keyed by ticket) so a bot restart doesn't
lose entry price / manual-override state mid-trade.

Rule (confirmed in design discussion, resume behavior refined 2026-09-04):
  - Untouched until the trade is `breakeven_trigger_points` in favor.
  - At that point, SL -> breakeven (entry price), and from that exact
    moment on, SL continuously follows M3's own FAR trail line (whichever
    of its two lines sits farther from price, see flip_state.far_near_line)
    minus/plus `sl_buffer`, on the trade's own direction.
  - SL only ever tightens -- never loosens, even if the far line itself
    retraces.
  - A manual SL EDIT (broker SL differs from what this manager itself
    last set, but is still a real value) pauses auto-trailing for that
    ticket.
  - The resume signal is specifically CLEARING the SL entirely (broker SL
    goes to None/0), not just changing it to some other value -- and
    clearing hands control back IMMEDIATELY, not on the next natural
    trigger (Option A, confirmed with the user over Option B): if the
    position is currently pre-breakeven, this manager re-establishes the
    same initial-SL formula fresh (current far line ∓ buffer) the very
    same cycle it notices the clear, rather than leaving the position
    with zero protection until +7 points arrives on its own. If already
    past breakeven, normal breakeven/far-line trailing just resumes.
  - This pause/resume cycle repeats indefinitely for the life of one
    trade -- change it again, it pauses again; clear it again, it
    resumes and re-protects again.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

_MIN_SL_IMPROVEMENT = 1e-6  # same float-noise tolerance as algo_v2/sl_manager.py


def _differs(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return a is not b
    return abs(a - b) > _MIN_SL_IMPROVEMENT


@dataclass
class PositionSLState:
    entry_price: float
    last_bot_sl: Optional[float]   # what this manager itself last confirmed set on the broker
    override_active: bool = False   # True while paused by a manual edit; cleared by a manual CLEAR


class SLManager:
    def __init__(self, path: str, breakeven_trigger_points: float, sl_buffer: float):
        self._path = Path(path)
        self._breakeven_trigger_points = breakeven_trigger_points
        self._sl_buffer = sl_buffer
        self._state: dict[int, PositionSLState] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for ticket_str, raw in data.get("positions", {}).items():
                self._state[int(ticket_str)] = PositionSLState(**raw)
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

    def compute(self, ticket: int, direction: int, entry_price: float, current_price: float,
               current_broker_sl: Optional[float], far_line: float) -> Optional[float]:
        """Returns a new SL to apply this cycle, or None if nothing should
        change. Does NOT assume the caller actually applied the returned
        value -- call confirm_applied() only after the broker call
        succeeds, same contract as algo_v2/sl_manager.py."""
        state = self._state.get(ticket)

        if state is None:
            # First sighting (just filled, or the bot restarted mid-trade)
            # -- baseline only, no proposal yet.
            self._state[ticket] = PositionSLState(entry_price=entry_price, last_bot_sl=current_broker_sl)
            self._save()
            return None

        if current_broker_sl is None:
            # SL manually cleared -- the resume signal. Hands control back
            # immediately (Option A): fall through to the protection logic
            # below instead of waiting for the next natural trigger.
            if state.override_active:
                print(f"[V5S-SL] #{ticket} SL manually cleared -- resuming auto-trail control")
            state.override_active = False
            state.last_bot_sl = None
        elif _differs(current_broker_sl, state.last_bot_sl):
            if not state.override_active:
                print(f"[V5S-SL] #{ticket} manual SL change detected "
                      f"({state.last_bot_sl} -> {current_broker_sl}) -- "
                      f"auto-trail paused until this SL is cleared entirely")
                state.override_active = True
            state.last_bot_sl = current_broker_sl  # track the human's value, don't fight it
            self._save()
            return None

        if state.override_active:
            self._save()
            return None

        favor = (current_price - entry_price) if direction == 1 else (entry_price - current_price)

        if favor < self._breakeven_trigger_points:
            if current_broker_sl is None:
                # Just resumed from a clear, still pre-breakeven -- the
                # position currently has ZERO protection. Re-establish the
                # same initial-SL formula fresh off the current far line
                # rather than leaving it bare until +7 points arrives.
                proposed = far_line - self._sl_buffer if direction == 1 else far_line + self._sl_buffer
                self._save()
                return proposed
            self._save()
            return None

        # Breakeven has triggered (or already had, e.g. right after a
        # resume) -- follow the far line, never proposing worse than
        # breakeven itself.
        far_side_sl = far_line - self._sl_buffer if direction == 1 else far_line + self._sl_buffer
        proposed = max(far_side_sl, entry_price) if direction == 1 else min(far_side_sl, entry_price)

        if current_broker_sl is not None:
            if direction == 1 and proposed <= current_broker_sl + _MIN_SL_IMPROVEMENT:
                self._save()
                return None
            if direction == -1 and proposed >= current_broker_sl - _MIN_SL_IMPROVEMENT:
                self._save()
                return None

        self._save()
        return proposed

    def confirm_applied(self, ticket: int, new_sl: float) -> None:
        """Call only after broker.modify_position_sl actually succeeds."""
        state = self._state.get(ticket)
        if state is not None:
            state.last_bot_sl = new_sl
            self._save()
