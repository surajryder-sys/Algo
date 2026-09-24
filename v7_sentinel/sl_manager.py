"""SL Manager: breakeven-then-far-trail-line trailing stop, per open
position. Persisted to disk (keyed by ticket) so a bot restart doesn't
lose entry price / manual-override state mid-trade. Ported verbatim
from v5_sentinel/sl_manager.py (2026-09-18) -- keyed by MT5 ticket
(globally unique per account, not per symbol), so no multi-instrument
change needed; a per-symbol state file (config.state_file_for()) is
still the right convention for consistency even though nothing here
would actually collide across symbols sharing one file.

Rule:
  - Breakeven triggers the moment EITHER of two things is true: partial
    booking has actually happened for this ticket (Trade Manager's own
    partial1/partial2, i.e. TradeManager.is_partially_cut()), OR the
    trade is currently `breakeven_trigger_points` (10 by default) in
    favor, checked directly here as a standalone points threshold. In
    the ORDINARY case these two conditions fire within about one poll
    cycle of each other anyway (partial1 itself also triggers at 10
    points) -- the standalone check matters specifically when
    partial-booking is delayed or paused (a manual TP pausing Trade
    Manager entirely, e.g.) and the position would otherwise sit
    unprotected past +10pts.
  - Once partial-booked, SL -> breakeven (entry price), and from that
    exact moment on, SL continuously follows M3's own FAR trail line
    (whichever of its two lines sits farther from price, see
    flip_state.far_near_line) minus/plus `sl_buffer`, on the trade's own
    direction.
  - SL only ever tightens -- never loosens, even if the far line itself
    retraces.
  - A manual SL EDIT (broker SL differs from what this manager itself
    last set, but is still a real value) pauses auto-trailing for that
    ticket.
  - The resume signal is specifically CLEARING the SL entirely (broker SL
    goes to None/0), not just changing it to some other value -- and
    clearing hands control back IMMEDIATELY: if the position is
    currently pre-breakeven, this manager re-establishes the same
    initial-SL formula fresh (current far line minus/plus buffer) the
    very same cycle it notices the clear, rather than leaving the
    position with zero protection until the breakeven trigger arrives on
    its own. If already past breakeven, normal breakeven/far-line
    trailing just resumes.
  - This pause/resume cycle repeats indefinitely for the life of one
    trade -- change it again, it pauses again; clear it again, it
    resumes and re-protects again.

  - PRE-BREAKEVEN FLIP CHECK (added 2026-09-22, user: "sl manager needs
    to check if any new lines available to trail from initial sl... when
    there's a flip in m3, sl manager can actually check and trail if
    there's a new qualifying sl, rest of the rules remain same" -- "flip
    means a bearish flip for sell trade and a bullish flip for a buy
    trade"): before this, once the INITIAL SL was set at entry, this
    manager never looked at it again until breakeven activated -- even if
    the trailing timeframe's own far line later moved to a genuinely
    tighter, valid position. Now, PRE-breakeven, if a fresh WITH-
    direction flip (flip_state.with_direction_flip_after()) has occurred
    on the trailing timeframe SINCE the position opened, this manager
    also checks the current far line for a tighter SL, using the exact
    same tighten-only comparison the post-breakeven trail already uses --
    just without the breakeven-price floor (not earned yet pre-
    breakeven). The caller computes and passes in this one boolean
    (with_direction_flip_after_entry) each cycle -- this module stays
    agnostic to which bridge/tracker/timeframe that flip came from, same
    as it already is for far_line itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

_MIN_SL_IMPROVEMENT = 1e-6  # same float-noise tolerance as algo_v2/sl_manager.py

# The broker rounds whatever SL we propose on fill (confirmed live in
# V5S: proposed 4416.61678, broker stored 4416.617, a ~0.0002 rounding
# difference) -- comparing that against the tight _MIN_SL_IMPROVEMENT
# tolerance made the manager detect ITS OWN just-applied SL as a
# "manual change" and permanently pause auto-trailing on the very next
# cycle. This tolerance is deliberately much wider (XAUUSD rounding
# error is a few ten-thousandths; 0.01 gives ~50x headroom) specifically
# for manual-change DETECTION -- the tight _MIN_SL_IMPROVEMENT above is
# unrelated and untouched, still governing whether a proposed trail
# update counts as a genuine improvement.
_MANUAL_CHANGE_TOLERANCE = 0.01


def _differs(a: Optional[float], b: Optional[float], tolerance: float = _MIN_SL_IMPROVEMENT) -> bool:
    if a is None or b is None:
        return a is not b
    return abs(a - b) > tolerance


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
               current_broker_sl: Optional[float], far_line: float, partial_booked: bool,
               with_direction_flip_after_entry: bool = False) -> Optional[float]:
        """Returns a new SL to apply this cycle, or None if nothing should
        change. Does NOT assume the caller actually applied the returned
        value -- call confirm_applied() only after the broker call
        succeeds.

        partial_booked: the caller's own TradeManager.is_partially_cut(ticket)
        for this SAME position -- ORs together with this class's own
        standalone points-in-favor check to decide whether breakeven is
        active, see module docstring for why both. Passed in rather than
        computed here since Trade Manager, not SL Manager, owns that
        bookkeeping.

        with_direction_flip_after_entry: see module docstring's own
        PRE-BREAKEVEN FLIP CHECK section -- True unlocks a one-condition
        pre-breakeven re-check of the far line (same tighten-only rule as
        post-breakeven), computed by the caller from its own flip-state
        tracker."""
        state = self._state.get(ticket)

        if state is None:
            # First sighting (just filled, or the bot restarted mid-trade)
            # -- baseline only, no proposal yet.
            self._state[ticket] = PositionSLState(entry_price=entry_price, last_bot_sl=current_broker_sl)
            self._save()
            return None

        if current_broker_sl is None:
            # SL manually cleared -- the resume signal. Hands control back
            # immediately: fall through to the protection logic below
            # instead of waiting for the next natural trigger.
            if state.override_active:
                print(f"[V7S-SL] #{ticket} SL manually cleared -- resuming auto-trail control")
            state.override_active = False
            state.last_bot_sl = None
        elif _differs(current_broker_sl, state.last_bot_sl, _MANUAL_CHANGE_TOLERANCE):
            if not state.override_active:
                print(f"[V7S-SL] #{ticket} manual SL change detected "
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
        breakeven_active = partial_booked or favor >= self._breakeven_trigger_points

        if not breakeven_active:
            if current_broker_sl is None:
                # Just resumed from a clear, still pre-breakeven -- the
                # position currently has ZERO protection. Re-establish the
                # same initial-SL formula fresh off the current far line
                # rather than leaving it bare until breakeven activates.
                proposed = far_line - self._sl_buffer if direction == 1 else far_line + self._sl_buffer
                self._save()
                return proposed
            if with_direction_flip_after_entry:
                # PRE-BREAKEVEN FLIP CHECK (see module docstring) -- same
                # tighten-only comparison post-breakeven uses, just without
                # the breakeven-price floor (not earned yet).
                proposed = far_line - self._sl_buffer if direction == 1 else far_line + self._sl_buffer
                if direction == 1 and proposed <= current_broker_sl + _MIN_SL_IMPROVEMENT:
                    self._save()
                    return None
                if direction == -1 and proposed >= current_broker_sl - _MIN_SL_IMPROVEMENT:
                    self._save()
                    return None
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
