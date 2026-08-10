"""Combined OB-edge + point-based SL trailing, with manual-override
detection that pauses BOTH methods together until a genuinely new event
(a fresh/different OB edge, or price making a new extreme since entry)
occurs. XAUUSD-specific for now -- the same concept is planned for the
other bots later, not yet wired in there.

Two trailing methods are evaluated every cycle, and whichever proposes
the more protective SL (higher for a BUY, lower for a SELL) wins -- this
was confirmed against several worked examples to reproduce "take
whichever event is more recent" without tracking recency explicitly: a
fresher edge is always also the numerically better one once favorable
price movement is involved, so picking-the-better-value and picking-the-
more-recent-event agree in every case that was worked through.

  OB-edge method: the caller computes this exactly as before
  (_direction_edges + entries.select_sl in main.py) and passes it in --
  this module doesn't duplicate that logic, it just consumes the result.

  Point method (constants below): once price is
  POINT_TRAIL_BREAKEVEN_TRIGGER points in favor of entry, SL floors at
  the entry price (breakeven). Once the running extreme price since
  entry exceeds entry by MORE than POINT_TRAIL_ACTIVATION points, SL
  instead trails at (running_extreme - POINT_TRAIL_GAP), recomputed
  every time a new extreme is reached -- pullbacks between new extremes
  never move it. POINT_TRAIL_ACTIVATION and POINT_TRAIL_GAP are both 10
  today but kept as separate constants since there's no reason they must
  stay equal. running_extreme is tracked from live tick price every
  poll, not candle highs/lows, and only ever moves in the favorable
  direction (max so far for a BUY, min so far for a SELL).

Manual override: if the broker's actual SL differs from what this
manager itself last set, that's a manual change. Both trailing methods
go silent for that position -- neither proposes anything -- until either
the OB candidate has changed from what it was at the moment of the
change, or price has made a new extreme beyond what running_extreme was
then. Whichever fires first clears the override and normal combined
trailing resumes from there.

All of this is per-position (keyed by ticket) and persisted to disk so a
bot restart doesn't lose entry price / running extreme / override state
mid-trade.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

POINT_TRAIL_BREAKEVEN_TRIGGER = 7.0
POINT_TRAIL_ACTIVATION = 10.0
POINT_TRAIL_GAP = 10.0

# Same floating-point tolerance as management.py's compute_trailing_sl, for
# the same reason (documented there): a bare > / < comparison against a
# broker-reported current SL can read a value that's ~1e-14 off the "same"
# price as a genuine improvement forever, generating an endless stream of
# identical modify calls. Real XAUUSD tick sizes are far larger than this.
_MIN_SL_IMPROVEMENT = 1e-6


def _differs(a: Optional[float], b: Optional[float]) -> bool:
    """True if a and b are genuinely different values, not just float noise
    on the same one -- same epsilon as the improvement check above. Handles
    None (no value at all, e.g. no OB edge) as a real difference from any
    actual number, but not from another None."""
    if a is None or b is None:
        return a is not b
    return abs(a - b) > _MIN_SL_IMPROVEMENT


@dataclass
class PositionSLState:
    entry_price: float
    running_extreme: float             # best price since entry: max for BUY, min for SELL
    last_bot_sl: Optional[float]       # what this manager itself last confirmed set on the broker
    override_active: bool = False
    override_ob_candidate: Optional[float] = None   # OB candidate snapshot at the moment override began
    override_extreme: Optional[float] = None        # running_extreme snapshot at that moment


class SLManager:
    def __init__(self, path: str):
        self._path = Path(path)
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
        """Drop tracked state for any ticket that's no longer an open
        position -- call once per cycle with the current live ticket set."""
        stale = [t for t in self._state if t not in open_tickets]
        if stale:
            for t in stale:
                del self._state[t]
            self._save()

    def _point_candidate(self, direction: int, entry_price: float, running_extreme: float) -> Optional[float]:
        favor = (running_extreme - entry_price) if direction == 1 else (entry_price - running_extreme)
        if favor < POINT_TRAIL_BREAKEVEN_TRIGGER:
            return None
        candidate = entry_price
        if favor > POINT_TRAIL_ACTIVATION:
            candidate = (running_extreme - POINT_TRAIL_GAP if direction == 1
                        else running_extreme + POINT_TRAIL_GAP)
        return candidate

    def compute(self, ticket: int, direction: int, entry_price: float, current_price: float,
               current_broker_sl: Optional[float], ob_candidate: Optional[float]) -> Optional[float]:
        """Returns a new SL to apply this cycle, or None if nothing should
        change. Call once per open position, every poll. Does NOT assume
        the caller actually applied the returned value -- call
        confirm_applied() afterwards only once the broker call succeeds,
        so a failed modify doesn't desync this manager's own record of
        what's really on the broker (which would otherwise misfire the
        manual-change detector on the very next cycle)."""
        state = self._state.get(ticket)

        if state is None:
            # First sighting of this position (just filled, or the bot
            # restarted mid-trade) -- baseline only, no proposal yet.
            initial_extreme = (max(entry_price, current_price) if direction == 1
                               else min(entry_price, current_price))
            self._state[ticket] = PositionSLState(
                entry_price=entry_price,
                running_extreme=initial_extreme,
                last_bot_sl=current_broker_sl,
            )
            self._save()
            return None

        # Running extreme is a pure price-history fact -- keeps tracking
        # every cycle regardless of override state.
        state.running_extreme = (max(state.running_extreme, current_price) if direction == 1
                                 else min(state.running_extreme, current_price))

        if _differs(current_broker_sl, state.last_bot_sl):
            if not state.override_active:
                print(f"[TRAIL] #{ticket} manual SL change detected "
                      f"({state.last_bot_sl} -> {current_broker_sl}) -- "
                      f"pausing auto-trail until a new OB or a new price extreme")
                state.override_active = True
                state.override_ob_candidate = ob_candidate
                state.override_extreme = state.running_extreme
            state.last_bot_sl = current_broker_sl  # track the human's value, don't fight it

        if state.override_active:
            new_ob_event = _differs(ob_candidate, state.override_ob_candidate)
            new_price_event = (
                (direction == 1 and state.running_extreme > state.override_extreme) or
                (direction == -1 and state.running_extreme < state.override_extreme)
            )
            if new_ob_event or new_price_event:
                print(f"[TRAIL] #{ticket} {'new OB' if new_ob_event else 'new price extreme'} "
                      f"-- resuming auto-trail")
                state.override_active = False
                state.override_ob_candidate = None
                state.override_extreme = None
            else:
                self._save()
                return None

        point_candidate = self._point_candidate(direction, state.entry_price, state.running_extreme)
        candidates = [c for c in (ob_candidate, point_candidate) if c is not None]
        if not candidates:
            self._save()
            return None

        proposed = max(candidates) if direction == 1 else min(candidates)

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
