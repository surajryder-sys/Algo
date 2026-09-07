"""HTF (Daily through M15) structure/levels store for the STR Reversal
Manager -- design confirmed with the user 2026-09-06/07, before any entry
logic exists. This module only builds the DATA layer: per-timeframe
Strong/Weak/Trap classification, and each timeframe's own two ATR trail
line values exposed as individual price levels. Touch detection, LTF
confirmation (M5/M3/M1), and trade execution are separate, later pieces.

Timeframe list (confirmed 2026-09-06): D1, H8, H6, H4, H3, H2, H1, M30,
M15. H7/H5/M45 were also requested but dropped -- not standard MT5
timeframes (no TIMEFRAME_H7/H5/M45 constant exists), and building them as
custom-resampled bars was explicitly declined in favor of just dropping
them, since there'd be no live MT5 chart to ever cross-check a custom
timeframe against anyway.

Classification and level roles (confirmed 2026-09-06) reuse flip_state.py
exactly -- same geometric test Trend Manager already relies on, just
reframed here as levels instead of a live bias:

  STRONG -- flip_state confirmed BULL, not watching. Both trail lines sit
  below the last close -- both act as SUPPORT.
  WEAK -- confirmed BEAR, not watching. Both lines above close -- both
  RESISTANCE.
  TRAP -- flip_state currently watching (straddling). One line above
  close (RESISTANCE), one below (SUPPORT) -- "no clear direction" per the
  user's own description.

Levels are always priced per-LINE against the last close directly (line
value vs. last_close), not derived indirectly via flip_state's far/near
convention -- this is deliberately the simpler, more literal reading of
"line above price is resistance, line below is support" the user gave,
and it holds correctly in all three character states above without
needing the near/far indirection at all.

"Only one trade per flip, resets at next flip" (confirmed 2026-09-06):
eligibility to trade a timeframe's current levels is tracked per
timeframe, keyed to that timeframe's own "character_event_time" -- the
bar_time its OWN flip_state last produced a FLIP/TRAP_RESOLVED (i.e. its
Strong/Weak/Trap character actually changing on ITS OWN bar close), NOT
a level merely being touched by price. LevelEligibilityStore persists
this to disk (JSON, keyed by timeframe) so a bot restart doesn't reopen
eligibility that had already been consumed, and resets automatically the
moment that timeframe's own character changes again.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v5_sentinel import flip_state, rates

# D1, H8, H6, H4, H3, H2, H1, M30, M15 -- confirmed 2026-09-06, in this order.
HTF_TIMEFRAMES_MINUTES = [1440, 480, 360, 240, 180, 120, 60, 30, 15]

# Human-readable short names for comments/logging -- matches the tag
# vocabulary agreed for Reversal Manager order comments (e.g. "H1/3C").
TIMEFRAME_NAMES = {
    1440: "D1", 480: "H8", 360: "H6", 240: "H4", 180: "H3",
    120: "H2", 60: "H1", 30: "M30", 15: "M15",
}


@dataclass(frozen=True)
class HTFLevel:
    timeframe_minutes: int
    value: float
    role: str          # "SUPPORT" or "RESISTANCE"
    line_no: int        # 1 or 2 -- which of the timeframe's own two trail lines this is


@dataclass(frozen=True)
class HTFState:
    timeframe_minutes: int
    character: str              # "STRONG" / "WEAK" / "TRAP"
    character_event_time: int    # bar_time this character last changed -- the eligibility key
    levels: tuple[HTFLevel, ...]
    last_close: float
    last_time: int


def _character(fs: "flip_state.FlipStateResult") -> str:
    if fs.watching is not None:
        return "TRAP"
    return "STRONG" if fs.confirmed == flip_state.Confirmed.BULL else "WEAK"


def _character_event_time(fs: "flip_state.FlipStateResult") -> int:
    """The bar_time this timeframe's OWN character last changed. Falls
    back to confirmed_since_time when last_event is None (the fetched
    window never saw a flip -- the whole window has been one character),
    so there's always a stable key to compare against, never None."""
    return fs.last_event.bar_time if fs.last_event is not None else fs.confirmed_since_time


def compute_htf_state(symbol: str, tf_minutes: int, **kwargs) -> Optional[HTFState]:
    """One timeframe's current character + levels. None if there isn't
    enough bar history yet (same contract as rates.read_atr_dual)."""
    series = rates.read_trail_series(symbol, tf_minutes, **kwargs)
    if series is None:
        return None
    fs = flip_state.compute(series)
    if fs is None:
        return None

    t1, t2 = series.trail1[-1], series.trail2[-1]
    levels = tuple(
        HTFLevel(
            timeframe_minutes=tf_minutes,
            value=value,
            role="RESISTANCE" if value > fs.last_close else "SUPPORT",
            line_no=line_no,
        )
        for line_no, value in ((1, t1), (2, t2))
        if value is not None
    )

    return HTFState(
        timeframe_minutes=tf_minutes,
        character=_character(fs),
        character_event_time=_character_event_time(fs),
        levels=levels,
        last_close=fs.last_close,
        last_time=fs.last_time,
    )


def compute_all_htf_states(symbol: str, **kwargs) -> dict[int, Optional[HTFState]]:
    """All 9 HTF timeframes at once, keyed by timeframe_minutes. A None
    value means that one didn't have enough bar history yet -- other
    timeframes are unaffected."""
    return {tf: compute_htf_state(symbol, tf, **kwargs) for tf in HTF_TIMEFRAMES_MINUTES}


class LevelEligibilityStore:
    """Persists, per HTF timeframe, the character_event_time last seen,
    which direction(s) have already been traded, and which of its 2
    individual lines is currently ARMED by a live-price touch -- see
    module docstring's "only one trade per flip" section and
    reversal_entry.py's "touch is live price, confirmation is candle
    close" design (confirmed 2026-09-07). A bot restart reloads this from
    disk rather than reopening eligibility that was already consumed
    mid-session, or forgetting a touch that armed a level before the
    restart.

    BUG FIXED 2026-09-07, found live: "traded_direction" used to be a
    single scalar, not a set -- in a TRAP character (support AND
    resistance both active at once, both commonly touched together in a
    tight consolidation), marking one direction traded silently
    UN-marked the other. That let the two directions repeatedly re-fire
    against each other -- confirmed live on H4 (07:24-08:19 IST, ~150
    trades, net -$62.21 on that timeframe alone) -- a real, continuous
    BUY<->SELL flip-flop, not a hypothetical. Now "traded_directions" is
    a list that both directions get appended to independently, so
    marking one never clears the other."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[int, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._state = {int(k): v for k, v in data.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._state = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._state))

    def _entry(self, tf_minutes: int) -> dict:
        """setdefault() only injects the new-schema default for a
        timeframe key that doesn't exist AT ALL yet -- an entry already
        persisted under the OLD schema (pre-2026-09-07, "traded_direction"
        singular) survives a restart with no "traded_directions" key at
        all, and would KeyError the moment mark_traded/mark_touched index
        it directly (confirmed live immediately after this same fix went
        in). The two setdefault() calls below backfill it lazily instead
        of requiring a clean wipe of the whole state file."""
        entry = self._state.setdefault(tf_minutes, {"event_time": None, "traded_directions": [], "touched_lines": []})
        entry.setdefault("traded_directions", [])
        entry.setdefault("touched_lines", [])
        return entry

    def sync(self, tf_minutes: int, character_event_time: int) -> bool:
        """Call once per cycle with a timeframe's CURRENT
        character_event_time. Returns True if this is a genuinely fresh
        character (differs from what was stored) -- in which case
        eligibility AND touch-armed state for that timeframe have just
        been reset (the old levels no longer even exist once the
        character changes, so any touch armed against them is moot).

        MONOTONIC GUARD, added 2026-09-07 (found live -- M30 fired a real
        SELL twice within what looked like the same unchanged character
        window, net -$41 on the intervening BUY that shouldn't have had
        the chance to exist): a genuinely NEW character can only ever
        advance to a LATER bar_time than what's already stored -- time
        doesn't move backwards. copy_rates HTF classification (this store
        deliberately has no bridge tie-breaker, see module docstring) can
        still be a path-dependent recompute that occasionally computes a
        transiently different character_event_time on one cycle and reverts
        the next (the same class of instability already documented for
        M3's own trail drift) -- if that happens, the reset-then-revert
        would silently wipe traded_directions in between, re-arming a
        direction that had already legitimately fired. Rejecting any
        "new" event_time that's OLDER than what's stored closes that gap:
        a real character change can never look like a regression, so
        anything that does is treated as a transient recompute glitch,
        not trusted."""
        entry = self._state.get(tf_minutes)
        stored_event_time = entry.get("event_time") if entry is not None else None

        if stored_event_time is not None and character_event_time < stored_event_time:
            print(f"[V5S-STR-ELIGIBILITY] M{tf_minutes} character_event_time regressed "
                  f"({stored_event_time} -> {character_event_time}) -- ignoring as a transient "
                  f"recompute glitch, not a genuine character change")
            return False

        if entry is None or stored_event_time != character_event_time:
            print(f"[V5S-STR-ELIGIBILITY] M{tf_minutes} character reset "
                  f"(event_time {stored_event_time} -> {character_event_time})")
            self._state[tf_minutes] = {
                "event_time": character_event_time, "traded_directions": [], "touched_lines": [],
            }
            self._save()
            return True
        return False

    def is_traded(self, tf_minutes: int, direction: int) -> bool:
        entry = self._state.get(tf_minutes)
        return entry is not None and direction in entry.get("traded_directions", [])

    def mark_traded(self, tf_minutes: int, direction: int) -> None:
        entry = self._entry(tf_minutes)
        if direction not in entry["traded_directions"]:
            entry["traded_directions"].append(direction)
        self._save()

    def mark_touched(self, tf_minutes: int, line_no: int) -> None:
        """Arms one specific line -- live price has reached it. Persists
        immediately so a restart doesn't lose an armed touch."""
        entry = self._entry(tf_minutes)
        if line_no not in entry["touched_lines"]:
            entry["touched_lines"].append(line_no)
            self._save()

    def is_touched(self, tf_minutes: int, line_no: int) -> bool:
        entry = self._state.get(tf_minutes)
        return entry is not None and line_no in entry.get("touched_lines", [])
