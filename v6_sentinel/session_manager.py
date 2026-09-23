"""Session Manager -- blocks NEW trade entries during fixed daily IST
windows (user's own 2026-09-24 spec, given as a discrete list of times).

Existing positions are NEVER touched by this -- SL trailing, Trade
Manager partials, Exit Manager closes all continue completely normally
throughout every window ("no fresh trades to be fired, existing trades
get managed, just no new trades in this window" -- user's own words,
repeated for every window). This ONLY ever skips the entry-signal-
detection step in each manager's own run_once(), the exact same
"if ... and not X:" gating shape trend_main.py already uses for its own
feed-staleness pause -- one more condition on the same gate, not a new
mechanism.

WINDOWS (all IST, inclusive of both boundary minutes -- e.g. "9:26-9:35"
is a full 10-minute window: 9:26, 9:27, ..., 9:35, matching the user's
own "10 min window" framing for each of these):
  - 02:16-03:45 (90 min) -- straddles the market's own 02:30-03:30 daily
    close (user's own note) with a 14-minute buffer before and a
    15-minute buffer after the actual closure.
  - 09:26-09:35 (10 min) -- New York midnight rollover.
  - 13:26-13:35 (10 min) -- London open.
  - 17:56-18:05 (10 min) -- New York open.
  - 18:56-19:05 (10 min) -- news window.

is_blocked() is stateless -- a pure function of wall-clock time, so a
mid-window process restart behaves correctly with no special-casing
(this is exactly why every other bridge-freshness/gate check in this
project is also a pure, stateless read rather than a persisted latch).

SessionWindowWatch adds a thin, in-memory-only (never persisted, same
convention trend_main.py's own _FeedWatch already uses) enter/exit EVENT
layer on top, purely so callers can print/alert ONCE per transition
instead of every single poll cycle -- it is never itself consulted for
the actual block decision.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class _Window:
    name: str
    start: tuple[int, int]   # (hour, minute), inclusive
    end: tuple[int, int]     # (hour, minute), inclusive


_WINDOWS = (
    _Window("late-night close straddle", (2, 16), (3, 45)),
    _Window("New York midnight rollover", (9, 26), (9, 35)),
    _Window("London open", (13, 26), (13, 35)),
    _Window("New York open", (17, 56), (18, 5)),
    _Window("news window", (18, 56), (19, 5)),
)


def is_blocked(now: Optional[datetime.datetime] = None) -> tuple[bool, Optional[str]]:
    """(blocked, window_name) -- window_name is None when not blocked.
    `now` defaults to the real current time; only ever overridden for
    tests. A naive `now` is assumed already UTC (matching every other
    epoch-based timestamp convention in this project) before converting
    to IST for the window comparison."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    ist_now = now.astimezone(_IST)
    hm = (ist_now.hour, ist_now.minute)
    for w in _WINDOWS:
        if w.start <= hm <= w.end:
            return True, w.name
    return False, None


class SessionWindowWatch:
    """Thin enter/exit EVENT wrapper around is_blocked() -- see module
    docstring. update() returns (blocked, window_name, event); callers
    use `blocked` for the actual gate and `event` only to decide whether
    to print/alert this cycle."""

    def __init__(self) -> None:
        self._was_blocked = False

    def update(self, now: Optional[datetime.datetime] = None) -> tuple[bool, Optional[str], Optional[str]]:
        """(blocked, window_name, event) -- event is "entered" the cycle
        blocked flips False->True, "left" the cycle it flips True->False,
        None otherwise."""
        blocked, window_name = is_blocked(now)
        event = None
        if blocked and not self._was_blocked:
            event = "entered"
        elif not blocked and self._was_blocked:
            event = "left"
        self._was_blocked = blocked
        return blocked, window_name, event
