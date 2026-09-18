"""HTF (Daily through M5) structure/levels store for RM-STR. Extended
2026-09-19 for RM-STR's own full redesign (confirmed with the user,
"big changes in RM STR"): this module now builds THREE independent
levels per timeframe -- the ATR dual-trail's own two lines (unchanged)
PLUS a native Supertrend line (rates.read_supertrend(), no chart/
indicator needed -- see that function's own fidelity caveat) -- instead
of just the ATR pair. Touch detection, LTF confirmation (M3/M5 CISD),
and trade execution are separate pieces (reversal_entry.py).

TIMEFRAME LIST REVISED 2026-09-19: D1, H4, H2, H1, M30, M15, M10, M5 --
H8/H6/H3 dropped, M10 added (confirmed a standard MT5 timeframe before
adding it -- mt5.TIMEFRAME_M10 exists). This is now RM-STR's OWN scope,
separate from the old 9-timeframe list this module used to carry.

Classification and level roles reuse flip_state.py exactly for the ATR
side -- same geometric test the bridge chain already relies on, just
reframed here as levels instead of a live bias:

  STRONG -- flip_state confirmed BULL, not watching. Both trail lines sit
  below the last close -- both act as SUPPORT.
  WEAK -- confirmed BEAR, not watching. Both lines above close -- both
  RESISTANCE.
  TRAP -- flip_state currently watching (straddling). One line above
  close (RESISTANCE), one below (SUPPORT) -- "no clear direction."

Supertrend has no trap state (always decisive, single line) -- its own
character is simply BULLISH (line below close -> SUPPORT) or BEARISH
(line above close -> RESISTANCE), same "line above price is resistance,
line below is support" rule applied to a single line instead of two.

Levels are always priced per-LINE against the last close directly (line
value vs. last_close), not derived indirectly via flip_state's far/near
convention -- the simpler, more literal reading of "line above price is
resistance, line below is support," and it holds correctly in every
character state above without needing the near/far indirection at all.

"Only one trade per flip, resets at next flip" -- confirmed still
applies to this redesign, user's own words: "a line moves, it doesn't
stay at a place like ob zones, so if the value is same, we are not
trading it, if value is different, it become eligible, or when it flips
it becomes eligible, traps and resolves lines can become eligible."
Two independent mechanisms together deliver this, both already proven
in the original design, now extended to also cover Supertrend as its
own independent source:
  1. mark_touched()/is_touched() key a touch to (source, line_no, value,
     role) -- the moment a line's own VALUE changes (it trails), the old
     touch simply stops matching and self-invalidates, no separate reset
     needed (a line "moving" is not the same event as it "flipping").
  2. LevelEligibilityStore.sync() resets ALL traded_directions for a
     given (timeframe, source) pair the moment THAT source's own
     character_event_time changes (a genuine FLIP or TRAP_RESOLVED,
     ATR-side only -- Supertrend has no trap so every character change
     there is necessarily a FLIP). ATR and Supertrend are tracked as
     SEPARATE (timeframe, source) eligibility entries since they flip on
     their own independent schedules -- a Supertrend flip never resets
     ATR's own traded_directions and vice versa.

Multi-instrument note: LevelEligibilityStore's _state dict is keyed by
(tf_minutes, source) ONLY, not (symbol, tf_minutes, source) -- same
pattern as bridge_bar_flip.BridgeBarFlipTracker. Safe ONLY because each
symbol gets its OWN store instance with its OWN state file (see
config.state_file_for()) -- never one instance shared across symbols.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v6_sentinel import flip_state, rates

# D1, H4, H2, H1, M30, M15, M10, M5 -- RM-STR's own scope, in this order.
HTF_TIMEFRAMES_MINUTES = [1440, 240, 120, 60, 30, 15, 10, 5]

# Human-readable short names for comments/logging -- matches the tag
# vocabulary agreed for Reversal Manager order comments (e.g. "H1/3C").
TIMEFRAME_NAMES = {
    1440: "D1", 240: "H4", 120: "H2", 60: "H1", 30: "M30", 15: "M15", 10: "M10", 5: "M5",
}

_SOURCE_ATR = "ATR"
_SOURCE_SUPERTREND = "SUPERTREND"


@dataclass(frozen=True)
class HTFLevel:
    timeframe_minutes: int
    value: float
    role: str          # "SUPPORT" or "RESISTANCE"
    source: str          # "ATR" or "SUPERTREND"
    line_no: int          # 1 or 2 for ATR (which of its two trail lines); always 1 for SUPERTREND (only one line)
    character_event_time: int  # THIS level's own source's last character-change bar_time -- the eligibility key for it specifically


@dataclass(frozen=True)
class HTFState:
    timeframe_minutes: int
    atr_character: str                    # "STRONG" / "WEAK" / "TRAP"
    atr_character_event_time: int
    supertrend_character: str               # "BULLISH" / "BEARISH" -- always decisive, no trap
    supertrend_character_event_time: int
    levels: tuple[HTFLevel, ...]              # 3 levels: 2 ATR + 1 Supertrend (fewer if one source has no data yet)
    last_close: float
    last_time: int


def _atr_character(fs: "flip_state.FlipStateResult") -> str:
    if fs.watching is not None:
        return "TRAP"
    return "STRONG" if fs.confirmed == flip_state.Confirmed.BULL else "WEAK"


def _atr_character_event_time(fs: "flip_state.FlipStateResult") -> int:
    """The bar_time this timeframe's OWN ATR character last changed.
    Falls back to confirmed_since_time when last_event is None (the
    fetched window never saw a flip -- the whole window has been one
    character), so there's always a stable key to compare against,
    never None."""
    return fs.last_event.bar_time if fs.last_event is not None else fs.confirmed_since_time


def compute_htf_state(symbol: str, tf_minutes: int, **kwargs) -> Optional[HTFState]:
    """One timeframe's current ATR + Supertrend character + levels. None
    if EITHER source doesn't have enough bar history yet (same contract
    as rates.read_atr_dual/read_supertrend) -- no partial states, matches
    this project's usual "no fallback, no guess" convention."""
    series = rates.read_trail_series(symbol, tf_minutes, **kwargs)
    if series is None:
        return None
    fs = flip_state.compute(series)
    if fs is None:
        return None

    st = rates.read_supertrend(symbol, tf_minutes)
    if st is None:
        return None

    t1, t2 = series.trail1[-1], series.trail2[-1]
    atr_character_event_time = _atr_character_event_time(fs)
    levels = [
        HTFLevel(
            timeframe_minutes=tf_minutes,
            value=value,
            role="RESISTANCE" if value > fs.last_close else "SUPPORT",
            source=_SOURCE_ATR,
            line_no=line_no,
            character_event_time=atr_character_event_time,
        )
        for line_no, value in ((1, t1), (2, t2))
        if value is not None
    ]
    levels.append(HTFLevel(
        timeframe_minutes=tf_minutes,
        value=st.supertrend,
        role="RESISTANCE" if st.supertrend > fs.last_close else "SUPPORT",
        source=_SOURCE_SUPERTREND,
        line_no=1,
        character_event_time=st.event_time,
    ))

    return HTFState(
        timeframe_minutes=tf_minutes,
        atr_character=_atr_character(fs),
        atr_character_event_time=atr_character_event_time,
        supertrend_character="BULLISH" if st.trend == 1 else "BEARISH",
        supertrend_character_event_time=st.event_time,
        levels=tuple(levels),
        last_close=fs.last_close,
        last_time=fs.last_time,
    )


def compute_all_htf_states(symbol: str, **kwargs) -> dict[int, Optional[HTFState]]:
    """All 8 HTF timeframes at once, keyed by timeframe_minutes. A None
    value means that one didn't have enough bar history yet -- other
    timeframes are unaffected."""
    return {tf: compute_htf_state(symbol, tf, **kwargs) for tf in HTF_TIMEFRAMES_MINUTES}


class LevelEligibilityStore:
    """Persists, per (HTF timeframe, source) pair, the
    character_event_time last seen, which direction(s) have already been
    traded, and which specific line is currently ARMED by a live-price
    touch -- see module docstring's "only one trade per flip" section.
    ATR and SUPERTREND are tracked as SEPARATE entries per timeframe
    (each flips on its own independent schedule) -- a key of
    "{tf_minutes}:{source}" keeps them from ever interfering with each
    other. A bot restart reloads this from disk rather than reopening
    eligibility that was already consumed mid-session, or forgetting a
    touch that armed a level before the restart.

    "traded_directions" is a list, not a scalar -- in a TRAP character
    (support AND resistance both active at once, both commonly touched
    together in a tight consolidation), marking one direction traded
    must never un-mark the other (a real V5S incident: a single-scalar
    field let two directions repeatedly re-fire against each other,
    ~150 trades, net -$62.21 on that timeframe alone). Both directions
    get appended to independently here."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[str, dict] = {}
        self._load()

    @staticmethod
    def _key(tf_minutes: int, source: str) -> str:
        return f"{tf_minutes}:{source}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._state = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._state = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._state))

    def _entry(self, tf_minutes: int, source: str) -> dict:
        """setdefault() only injects the new-schema default for a key
        that doesn't exist AT ALL yet -- an entry already persisted under
        an older schema survives a restart with no "traded_directions"/
        "touched_lines" key at all, and would KeyError the moment
        mark_traded/mark_touched index it directly. The setdefault()
        calls below backfill it lazily instead of requiring a clean wipe
        of the whole state file."""
        key = self._key(tf_minutes, source)
        entry = self._state.setdefault(key, {"event_time": None, "traded_directions": [], "touched_lines": []})
        entry.setdefault("traded_directions", [])
        entry.setdefault("touched_lines", [])
        return entry

    def sync(self, tf_minutes: int, source: str, character_event_time: int) -> bool:
        """Call once per cycle with a (timeframe, source) pair's CURRENT
        character_event_time. Returns True if this is a genuinely fresh
        character (differs from what was stored) -- in which case
        eligibility AND touch-armed state for THAT (timeframe, source)
        pair have just been reset (the old level(s) that source produced
        no longer even exist once its own character changes, so any
        touch armed against them is moot). ATR and SUPERTREND for the
        SAME timeframe are independent -- resetting one never touches
        the other's own stored entry.

        MONOTONIC GUARD: a genuinely NEW character can only ever advance
        to a LATER bar_time than what's already stored -- time doesn't
        move backwards. copy_rates HTF classification (this store
        deliberately has no bridge tie-breaker) can still be a
        path-dependent recompute that occasionally computes a
        transiently different character_event_time on one cycle and
        reverts the next -- if that happens, the reset-then-revert would
        silently wipe traded_directions in between, re-arming a
        direction that had already legitimately fired. Rejecting any
        "new" event_time that's OLDER than what's stored closes that
        gap: a real character change can never look like a regression,
        so anything that does is treated as a transient recompute
        glitch, not trusted."""
        key = self._key(tf_minutes, source)
        entry = self._state.get(key)
        stored_event_time = entry.get("event_time") if entry is not None else None

        if stored_event_time is not None and character_event_time < stored_event_time:
            print(f"[V6S-STR-ELIGIBILITY] M{tf_minutes}/{source} character_event_time regressed "
                  f"({stored_event_time} -> {character_event_time}) -- ignoring as a transient "
                  f"recompute glitch, not a genuine character change")
            return False

        if entry is None or stored_event_time != character_event_time:
            print(f"[V6S-STR-ELIGIBILITY] M{tf_minutes}/{source} character reset "
                  f"(event_time {stored_event_time} -> {character_event_time})")
            self._state[key] = {
                "event_time": character_event_time, "traded_directions": [], "touched_lines": [],
            }
            self._save()
            return True
        return False

    def is_traded(self, tf_minutes: int, source: str, direction: int) -> bool:
        entry = self._state.get(self._key(tf_minutes, source))
        return entry is not None and direction in entry.get("traded_directions", [])

    def mark_traded(self, tf_minutes: int, source: str, direction: int) -> None:
        entry = self._entry(tf_minutes, source)
        if direction not in entry["traded_directions"]:
            entry["traded_directions"].append(direction)
        self._save()

    _TOUCH_VALUE_TOLERANCE = 0.01  # float/recompute jitter only, not a real level move

    def mark_touched(self, tf_minutes: int, source: str, line_no: int, value: float, role: str) -> None:
        """Arms one specific line -- live price has reached it. Persists
        immediately so a restart doesn't lose an armed touch.

        The (value, role) actually tested is stored alongside line_no,
        and is_touched() below requires them to still match the level's
        CURRENT value/role -- a level's slot (line_no) can get relabeled
        SUPPORT<->RESISTANCE on the very next bar close without the
        timeframe's own character_event_time moving at all (entering a
        trap never sets last_event, ATR side only), so a touch recorded
        against the OLD value/role must not silently keep counting
        against whatever that slot means now. If the level moved on, the
        old touch simply no longer counts, self-invalidating with no
        separate reset step needed -- this is also what makes a
        TRAILING line eligible again the instant it moves, per the
        user's own "if the value is different, it becomes eligible"."""
        entry = self._entry(tf_minutes, source)
        for existing in entry["touched_lines"]:
            if existing["line_no"] == line_no and existing["role"] == role \
                    and abs(existing["value"] - value) < self._TOUCH_VALUE_TOLERANCE:
                return  # already armed against this exact (line_no, value, role) -- nothing new to persist
        entry["touched_lines"] = [e for e in entry["touched_lines"] if e["line_no"] != line_no]
        entry["touched_lines"].append({"line_no": line_no, "value": value, "role": role})
        self._save()

    def is_touched(self, tf_minutes: int, source: str, line_no: int, value: float, role: str) -> bool:
        entry = self._state.get(self._key(tf_minutes, source))
        if entry is None:
            return False
        for existing in entry.get("touched_lines", []):
            if existing["line_no"] == line_no and existing["role"] == role \
                    and abs(existing["value"] - value) < self._TOUCH_VALUE_TOLERANCE:
                return True
        return False
