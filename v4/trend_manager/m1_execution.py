"""M1 execution logic for V4's Trend Manager, per the user's explicit
rules 2026-08-28 (M5/M3 parent-bias gating explicitly dropped the same
day -- "no need of m3 and m5 now" -- this is M1-only).

Rule, in full:
  - "flip" = M1 price CLOSE moving above (buy) or below (sell) BOTH ATR
    trail lines -- a candle crossing only ONE line is "undecisive," not a
    signal. Two independent sources are raced for this, since either can
    confirm first: MT5-native (mql5/SurajBot_ATRTrail_..._DUAL.mq5's
    bridge) and TradingView (v3/tv_scraper's dual-line fix, same
    STRONG/WEAK/UNDECISIVE rule). Whichever confirms a NEW direction
    first wins; if both eventually confirm the same direction that's not
    a conflict, just redundant confirmation of the same move; if they
    actively disagree (one STRONG, one WEAK at the same poll) this
    deliberately does nothing rather than guess.
  - The zone-based edge-gap filter (a minimum 5-point gap between the
    flip candle's close and the nearest opposing OB zone edge) was
    REMOVED ENTIRELY 2026-08-31, user's explicit request -- "remove zone
    block technique entirely, i wanna deploy a new technique for
    blocking orders." Zone data (M1's own, the MT5-native M5 zones, and
    the wider TV-scraper H4-M5 buffer zones) is still read and logged by
    main.py for support/resistance visibility -- "have the zone data to
    identify resistance and support of order blocks" -- but is no longer
    passed into this module or used to gate entries in any way. A fresh,
    non-stale flip now fires unconditionally (subject only to the
    one-shot-per-phase and staleness rules below) until a replacement
    blocking technique is built.
  - Initial SL = whichever MT5 ATR trail line is FARTHER from current
    price (the more conservative one), +2.0-point buffer. SL is anchored
    to MT5's own trail values specifically (not TradingView's) since the
    account's own broker prices are what a real MT5 stop-loss is set
    against.
  - A flip is resolved (entered OR permanently skipped) exactly ONCE per
    directional phase -- "trade strictly should be executed on a flip
    candle, not on any random candle" means one evaluation window per
    phase, not a rolling re-check on every later poll that still shows
    the same confirmed direction. Tracked via the ACTIVE DIRECTION itself
    (not a raw event_time) specifically because two independently-clocked
    sources don't share one comparable timestamp -- see
    V4ExecutionState's own docstring.
  - A MANUALLY closed trade blocks that exact directional phase from
    re-entering until it genuinely flips away and (potentially) back --
    see mark_manually_closed's own docstring for what "manual" detection
    this depends on (not built here).
  - A flip older than MAX_FLIP_AGE_SECONDS is ignored outright, entirely
    separate from the one-shot-per-phase tracking above -- confirmed live
    2026-08-28: a state reset made a ~10-minute-old flip look brand new
    and fire at a stale, drifted price ("it fired on current candle, i
    need trade only on the flipped candle, not any random candle"). This
    check is against WALL-CLOCK time specifically, not "is this new to
    our state," so it holds regardless of restarts or state resets.
    Raised 60s->90s 2026-08-29 after confirming live that 60s gave zero
    margin over the mandatory bar-open-to-close delay itself (every flip
    was born already 60-62s old) and silently killed every single flip --
    see MAX_FLIP_AGE_SECONDS's own comment for the full timing evidence.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from v4.bridge.reader import ATRDualSnapshot
from v4.bridge.tv_atr import TVStructure

Direction = Literal["buy", "sell"]
Source = Literal["mt5", "tv", "both"]

SL_BUFFER_POINTS = 2.0
# A flip must be acted on within this many seconds of its own bar closing
# -- confirmed live 2026-08-28: resetting active_direction to clear a bug
# made a ~10-minute-old (704s) WEAK flip look "brand new" to this module,
# firing a real order at whatever price was current at that moment
# instead of the flip candle's own price -- "not any random candle."
# Same class of bug v3 already hit with stale retests (fixed there with
# an absolute 30-minute recency check). Tightened 2026-08-28 from 120s (2
# M1 bars) to 60s (1 bar) per explicit request -- "make it on the same
# candle... right immediately after the next candle's immediate opening":
# a flip must be acted on within the SAME bar-close-to-next-bar-open
# transition it happened in, not one bar later. This is deliberately an
# ABSOLUTE check against wall-clock time, not just "is this new to our
# own state" -- the whole point is that it can't be defeated by a state
# reset/restart the way a pure novelty check can.
#
# Raised 2026-08-29 from 60s -> 90s: confirmed live that 60s left ZERO
# margin and silently killed EVERY single flip since the tighten above --
# structure_event_time is the flip bar's OPEN time (standard MT5
# convention), so a M1 bar can only be confirmed closed once the NEXT bar
# opens, i.e. at event_time+60s at the absolute earliest, before this
# module can look at it even once. Checked three separate live flips
# (23:26/00:31/00:51 IST) -- all three were first observed by this
# process at exactly 61-62s old, never once under 60s, because the
# mandatory bar-close wait alone already consumes the whole budget before
# indicator-publish + poll latency even get added on top. 90s keeps the
# "same candle, not one bar later" intent (still well under 2 bars) while
# leaving ~28-30s of real margin instead of a negative one.
MAX_FLIP_AGE_SECONDS = 90.0


@dataclass
class EntryDecision:
    direction: Direction
    initial_sl: float
    far_line: str    # "line1" | "line2" -- which MT5 trail line the SL is anchored to
    source: Source   # which feed(s) confirmed this flip


@dataclass
class EvaluationResult:
    """What evaluate_entry actually did this poll, and WHY -- added
    2026-08-28 after repeatedly needing to manually reverse-engineer why
    a real flip didn't produce a trade ("why to restart, i mean it should
    have the problem written, why the trade was not executed, and if
    executed, the reason for execution as well"). decision is None for
    every outcome except a genuine entry; reason is ALWAYS populated,
    for every single code path, so the live log is a complete
    self-explaining audit trail without needing a separate investigation
    each time."""
    decision: Optional[EntryDecision]
    reason: str


def _direction_from_structure(structure: Optional[str]) -> Optional[Direction]:
    if structure == "STRONG":
        return "buy"
    if structure == "WEAK":
        return "sell"
    return None


class V4ExecutionState:
    """Tracks the CURRENTLY ACTED-ON direction, not a raw event_time --
    deliberately, because MT5 and TradingView each stamp their own flip's
    bar time on their own independent clock, so "is this the same flip
    I've already seen" can't be answered by comparing two sources'
    event_times directly. Tracking the resolved DIRECTION instead sidesteps
    that entirely: once a direction has been acted on (entered, or
    permanently skipped for failing the edge-gap filter), neither source
    re-triggers it again until the direction genuinely reverses -- which
    is exactly "one evaluation per flip candle" restated in a form that
    works across two clocks.

    Persisted the same JSON-file way as every other state store in this
    repo, so a restart resumes exactly where it left off."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._active_direction: Optional[Direction] = None
        self._blocked_direction: Optional[Direction] = None
        # True only once a REAL position has actually been confirmed open
        # for the current active_direction (set by mark_position_opened,
        # called right after a successful order send) -- distinct from
        # active_direction itself, which can also be "resolved" by the
        # edge-gap filter REJECTING a phase without ever opening a real
        # position. See reconcile()'s own docstring for why this
        # distinction is the whole point of the 2026-08-28 fix.
        self._position_open: bool = False
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._active_direction = raw.get("active_direction")
            self._blocked_direction = raw.get("blocked_direction")
            self._position_open = bool(raw.get("position_open", False))
        except (json.JSONDecodeError, OSError):
            self._active_direction = None
            self._blocked_direction = None
            self._position_open = False

    def _save(self) -> None:
        self._path.write_text(json.dumps({
            "active_direction": self._active_direction,
            "blocked_direction": self._blocked_direction,
            "position_open": self._position_open,
        }))

    def already_active(self, direction: Direction) -> bool:
        return self._active_direction == direction

    def is_blocked(self, direction: Direction) -> bool:
        return self._blocked_direction == direction

    def set_active_direction(self, direction: Direction) -> None:
        """Records a direction as resolved (entered or permanently
        skipped) for this phase. A genuinely NEW direction (a real
        reversal) clears any old block -- the block only ever applies to
        the specific phase it was set during, per "blocked until next
        flip." Also resets position_open to False -- a fresh phase starts
        with no confirmed position until mark_position_opened says
        otherwise."""
        if direction != self._active_direction:
            self._active_direction = direction
            self._blocked_direction = None
            self._position_open = False
        self._save()

    def mark_position_opened(self) -> None:
        """Call right after a real order for the current active_direction
        is actually sent successfully -- see reconcile()'s own docstring
        for why this matters."""
        self._position_open = True
        self._save()

    def mark_manually_closed(self) -> None:
        """Call once something detects the position for the CURRENT
        active direction was closed by the user, not by this system's own
        SL/exit logic -- blocks re-entry on this exact phase until it
        genuinely reverses. Detecting "was this close manual" is a
        separate, not-yet-built reconciliation concern (Execution Bridge
        territory, same shape as v3's own manual-cancel/close detection)
        -- this method only records the resulting block once that
        detection exists and calls it."""
        if self._active_direction is not None:
            self._blocked_direction = self._active_direction
            self._save()

    def reconcile(self, has_open_position: bool) -> None:
        """Call once per poll with whether a REAL matching position
        currently exists in MT5. Fixes a real bug confirmed live
        2026-08-28: a position closed via SL at 21:22:04, but
        active_direction stayed "sell" forever after (nothing ever
        cleared it on a close) -- so a genuinely fresh WEAK flip at
        21:25:00 was silently swallowed by already_active("sell") even
        though no position had existed for 3 minutes. Only resets state
        on an actual open-to-closed TRANSITION (position_open was True,
        now false) -- deliberately does NOT reset just because no
        position exists, since a phase the edge-gap filter rejected
        (never opened a real position at all) must stay resolved and NOT
        get re-evaluated every single poll, per the "one evaluation per
        flip" rule elsewhere in this module."""
        if self._position_open and not has_open_position:
            self._active_direction = None
            self._blocked_direction = None
            self._position_open = False
            self._save()


def _far_line(atr: ATRDualSnapshot, current_price: float) -> tuple[str, float]:
    """Whichever MT5 trail line is farther from current price -- the
    wider, more conservative one -- is the initial SL anchor, per the
    explicit rule (never the near one)."""
    d1 = abs(current_price - atr.line1.trail_stop)
    d2 = abs(current_price - atr.line2.trail_stop)
    return ("line1", atr.line1.trail_stop) if d1 >= d2 else ("line2", atr.line2.trail_stop)


def _initial_sl(direction: Direction, far_trail_stop: float) -> float:
    return far_trail_stop - SL_BUFFER_POINTS if direction == "buy" else far_trail_stop + SL_BUFFER_POINTS


def evaluate_entry(
    state: V4ExecutionState,
    mt5_atr: ATRDualSnapshot,
    tv_structure: Optional[TVStructure],
    previous_candle_close: float,
    current_price: float,
    now: Optional[float] = None,
) -> EvaluationResult:
    """Call once per poll. `now` defaults to the real wall clock; only
    overridden by tests that need to control staleness precisely.

    result.decision is populated only on the exact poll where a genuinely
    NEW directional phase (a) is confirmed by at least one of the two ATR
    sources AND that source's flip is no older than MAX_FLIP_AGE_SECONDS
    (agreeing sources, not conflicting; a stale source is treated as if
    it hadn't confirmed at all), and (b) hasn't been resolved yet. No
    zone-based filter applies anymore -- removed entirely 2026-08-31, see
    this module's own top docstring.

    result.reason is ALWAYS populated, on every single code path
    (including a genuine entry) -- see EvaluationResult's own docstring
    for why: this is the whole audit trail, not just the happy path."""
    if now is None:
        now = time.time()

    mt5_dir_raw = _direction_from_structure(mt5_atr.structure)
    tv_dir_raw = _direction_from_structure(tv_structure.state) if tv_structure is not None else None

    mt5_age = now - mt5_atr.structure_event_time
    mt5_fresh = mt5_age <= MAX_FLIP_AGE_SECONDS
    tv_fresh = tv_structure is not None and (now - tv_structure.event_time) <= MAX_FLIP_AGE_SECONDS

    mt5_dir = mt5_dir_raw if mt5_fresh else None
    tv_dir = tv_dir_raw if (tv_structure is not None and tv_fresh) else None

    if mt5_dir_raw is not None and not mt5_fresh:
        # Still resolves the phase lock to THIS direction even though
        # nothing fires -- fixed 2026-08-31, confirmed live: this used to
        # return without ever touching active_direction, so a flip that
        # arrived already dead-on-arrival (born >90s stale -- a real
        # incident: 376s old on its very first poll) left the OPPOSITE,
        # much older direction's lock in place indefinitely. The market
        # then genuinely reversed again, TWICE, and both fresh, otherwise
        # valid flips were silently swallowed as "already active this
        # phase" -- leftovers from a resolution that happened hours
        # earlier in the opposite direction, because nothing had ever
        # released it. A stale flip is still a REAL, confirmed change of
        # direction on the chart even if it's too old to act on -- so it
        # resolves this phase (skipped, not entered) the same way an
        # edge-gap rejection or a successful entry already does,
        # correctly clearing the way for the next genuinely fresh flip.
        state.set_active_direction(mt5_dir_raw)
        return EvaluationResult(None, f"mt5 {mt5_dir_raw} flip is stale ({mt5_age:.0f}s old, "
                                       f"limit {MAX_FLIP_AGE_SECONDS:.0f}s) -- ignored")

    if mt5_dir is not None and tv_dir is not None and mt5_dir != tv_dir:
        return EvaluationResult(None, f"sources disagree (mt5={mt5_dir}, tv={tv_dir}) -- waiting, not guessing")

    direction = mt5_dir or tv_dir
    if direction is None:
        return EvaluationResult(None, f"no confirmed flip (mt5={mt5_atr.structure}, "
                                       f"tv={tv_structure.state if tv_structure else 'n/a'})")

    source: Source = "both" if (mt5_dir is not None and tv_dir is not None) else ("mt5" if mt5_dir else "tv")

    if state.already_active(direction):
        return EvaluationResult(None, f"{direction} already active this phase -- no re-fire")
    if state.is_blocked(direction):
        state.set_active_direction(direction)
        return EvaluationResult(None, f"{direction} blocked (manually closed previously) -- skipped")

    # Zone-based edge-gap filter removed entirely here -- 2026-08-31, see
    # this module's own top docstring. No blocking check between this
    # point and the decision below; a fresh, non-stale, unresolved flip
    # always enters.
    far_line, far_trail_stop = _far_line(mt5_atr, current_price)
    decision = EntryDecision(
        direction=direction,
        initial_sl=_initial_sl(direction, far_trail_stop),
        far_line=far_line,
        source=source,
    )
    state.set_active_direction(direction)
    return EvaluationResult(
        decision,
        f"{direction} ENTERED: source={source}, flip confirmed by "
        f"{'both feeds' if source == 'both' else source}, sl anchored to {far_line}",
    )
