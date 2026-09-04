"""Trade Manager: direct %-based profit booking, no bot-placed TP ever.

Rule (confirmed in design discussion):
  - +10 points in favor -> close 70% of the ORIGINAL entry volume.
  - +15 points in favor -> close another 15% of the ORIGINAL entry volume.
  - Remaining 15% rides on SL Manager's trailing SL, no fixed TP.
  - Both thresholds are level checks re-evaluated every cycle (not
    one-shot edge triggers) -- this is what makes the TP-pause/resume
    rule below fall out for free, no special catch-up logic needed.
  - If the position currently carries a broker-side TP (placed manually
    -- the bot itself never sets one, see broker.send_market_order),
    Trade Manager pauses ENTIRELY: no automatic %-exits at all while
    that TP exists, even past a threshold. Removing the manual TP
    resumes normal evaluation from whatever the current price is.
    Re-placing a TP pauses again. Fully reactive to current TP presence,
    not a one-time latch.

Position-lifecycle decisions (square-off on a valid opposite setup, or on
a same-direction resolved trap once partially cut) live in main.py, which
has both M5 Bias and M3 flip_state available -- this module only ever
manages the CURRENT open position's own profit-booking progress.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class PositionTMState:
    entry_price: float
    original_volume: float
    partial1_done: bool = False
    partial2_done: bool = False
    entry_comment: str = ""   # captured at first sighting -- MT5 overwrites the
                               # position's own comment field on every partial
                               # close (confirmed live, no order_send action can
                               # override that on this broker), so this is the
                               # only place the original entry rationale survives.
                               # Default "" keeps old persisted records (from
                               # before this field existed) loading without error.


def _round_volume(volume: float, volume_step: float) -> float:
    """Rounds to the NEAREST valid step, not floor -- e.g. 0.15 * 0.06 lots
    = 0.009, which must round to 0.01 (not down to 0.00 and silently skip
    the booking entirely). Confirmed against the exact XAUUSD sizing
    given: 0.06 lots -> 70%=0.04, 15%=0.01, 15%=0.01."""
    if volume_step <= 0:
        return volume
    steps = round(volume / volume_step)
    return round(steps * volume_step, 8)


class TradeManager:
    def __init__(self, path: str, partial1_trigger_points: float, partial1_fraction: float,
                partial2_trigger_points: float, partial2_fraction: float):
        self._path = Path(path)
        self._partial1_trigger = partial1_trigger_points
        self._partial1_fraction = partial1_fraction
        self._partial2_trigger = partial2_trigger_points
        self._partial2_fraction = partial2_fraction
        self._state: dict[int, PositionTMState] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for ticket_str, raw in data.get("positions", {}).items():
                self._state[int(ticket_str)] = PositionTMState(**raw)
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

    def is_partially_cut(self, ticket: int) -> bool:
        """True once at least one of the two partial-booking stages has
        fired for this ticket -- used by main.py's same-direction
        fresh-signal rule (only refresh a position that's already been
        cut down; a still-full-size matching position has nothing to
        refresh)."""
        state = self._state.get(ticket)
        return state is not None and (state.partial1_done or state.partial2_done)

    def get_entry_comment(self, ticket: int) -> Optional[str]:
        """The position's own comment as it read at first sighting -- i.e.
        before any partial close could have overwritten it on the broker
        side. None if this ticket was never seen (or seen before this
        field existed)."""
        state = self._state.get(ticket)
        return (state.entry_comment or None) if state is not None else None

    def evaluate(self, ticket: int, direction: int, entry_price: float, current_price: float,
                current_volume: float, has_manual_tp: bool, volume_step: float,
                entry_comment: str = "") -> Optional[tuple[float, str]]:
        """Returns (volume_to_close, label) for THIS cycle's partial close,
        or None if nothing to do. label is "partial1"/"partial2", purely
        for logging/comments. entry_comment is only ever used on first
        sighting (see get_entry_comment) -- passing it on later calls is
        harmless, it's just ignored once the state already exists."""
        state = self._state.get(ticket)
        if state is None:
            # First sighting -- original_volume is whatever's open right
            # now (assumes this is caught before any partial close has
            # ever happened for this ticket, i.e. polled at least once
            # between fill and the first possible +10pt threshold). Same
            # assumption already applies to entry_comment.
            state = PositionTMState(entry_price=entry_price, original_volume=current_volume,
                                    entry_comment=entry_comment)
            self._state[ticket] = state
            self._save()

        if has_manual_tp:
            return None  # paused entirely, no state progress either

        favor = (current_price - entry_price) if direction == 1 else (entry_price - current_price)

        if not state.partial1_done and favor >= self._partial1_trigger:
            vol = _round_volume(state.original_volume * self._partial1_fraction, volume_step)
            vol = min(vol, current_volume)
            if vol > 0:
                state.partial1_done = True
                self._save()
                return vol, "partial1"

        if state.partial1_done and not state.partial2_done and favor >= self._partial2_trigger:
            vol = _round_volume(state.original_volume * self._partial2_fraction, volume_step)
            vol = min(vol, current_volume)
            if vol > 0:
                state.partial2_done = True
                self._save()
                return vol, "partial2"

        return None
