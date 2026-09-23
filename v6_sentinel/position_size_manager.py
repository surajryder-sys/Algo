"""Position Size Manager -- halves position sizing during a fixed daily
IST night window, and switches Trade Manager to a full-close-at-10-
points booking mode for the same window (user's own 2026-09-24 spec).

WINDOW: 23:00-04:00 IST, inclusive of both boundary minutes (crosses
midnight -- see is_night()'s own wrap-around handling). Stateless, a
pure function of wall-clock time, same convention session_manager.py
already uses -- safe across a mid-window restart, no special-casing
needed.

SIZING: each manager's own config stores its own explicit night_lots
value (same "one deliberately-tuned number per symbol, no guessing"
convention every other per-symbol default in this project already uses)
-- this module never picks a lot size itself, only answers "is it night
right now." XAUUSD: TM-STR/RM-STR/RM-ICT go from 0.06 to 0.03 (an exact
half); Scalper from 0.05 to 0.03 (the user's own explicit number, NOT a
literal half of 0.05 -- presumably rounded up to the nearest 0.01 lot
step, but recorded here verbatim rather than computed, matching every
other "explicit number, don't derive it" default in this project).

PROFIT BOOKING (TM-STR/RM-STR/RM-ICT only -- NOT Scalper, which already
has its own broker-side 1:1 TP and "follows its own TP" unchanged
during this window, per the user's own words): NIGHT_PROFIT_TARGET_POINTS
replaces Trade Manager's own Type1/Type2 partial-booking scheme entirely
for the duration of the window -- a single FULL close the instant a
position reaches this many points in profit, no partials, no dynamic
Type1/Type2 switching. Every other rule (SL Manager's own trailing/
breakeven, Exit Manager's four components, the Sideways Trapper) is
completely untouched by this -- "remaining exit rules and other logics
remain untouched" (user's own words). Applied purely on CURRENT wall-
clock time each cycle, regardless of when the position itself opened
(same "live, continuous check" convention every other gate in this
project already uses) -- a position opened at 22:00 that's still open
at 23:00 switches to night-mode booking right then, same as one opened
at 23:30.
"""
from __future__ import annotations

import datetime
from typing import Optional

NIGHT_PROFIT_TARGET_POINTS = 10.0

_START = (23, 0)   # inclusive
_END = (4, 0)      # inclusive
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def is_night(now: Optional[datetime.datetime] = None) -> bool:
    """True during the 23:00-04:00 IST window (inclusive both ends,
    crosses midnight). `now` defaults to the real current time; only
    ever overridden for tests. A naive `now` is assumed already UTC,
    matching every other epoch-based timestamp convention here."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    ist_now = now.astimezone(_IST)
    hm = (ist_now.hour, ist_now.minute)
    # Wraps midnight: 23:00-23:59 OR 00:00-04:00.
    return hm >= _START or hm <= _END


def points_profit(direction: int, entry_price: float, current_price: float) -> float:
    """Profit in raw price points (not yet lot/pip-value adjusted) --
    positive current_price - entry_price for a BUY, the mirror for a
    SELL. Matches the same "points" unit every other trigger in this
    project (breakeven_trigger_points, partialN_trigger_points, ...)
    already uses."""
    return (current_price - entry_price) if direction == 1 else (entry_price - current_price)


def night_target_hit(direction: int, entry_price: float, current_price: float) -> bool:
    """True if this position has reached the night-mode full-close
    target -- see module docstring's own PROFIT BOOKING section."""
    return points_profit(direction, entry_price, current_price) >= NIGHT_PROFIT_TARGET_POINTS
