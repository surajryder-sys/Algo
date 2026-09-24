"""TM-STR entry engine.

  ENTRY -- M5 (primary): ONLY a fresh M5 CISD triggers an entry -- an M5 ATR
  flip is NOT a trigger (asked and answered explicitly). Uses
  cisd_bridge.fresh_cisd() (the "privileged, momentary, only non-None the
  EXACT bar it confirmed" contract), so a CISD that is merely still standing
  from earlier never fires, and its direction must be in the M15 gate's
  currently allowed set (trend_bias.compute_gate() -- structure and CISD
  agree -> only that one direction; they disagree -> both directions
  allowed). M5's OWN ATR state is NOT consulted at all for this.

  ENTRY -- M3 (added 2026-09-22, its own STRICTER rule, user's own words:
  "if m5 bullish, m15 bullish, m3 can fire trade on cisd event" / mirrored
  for bearish): a fresh M3 CISD only fires when BOTH of these hold, not just
  "in gate.allowed" --
    1. the M15 gate is in STRICT agreement with the CISD's direction (the
       AGREE case specifically -- structure AND CISD both point that way;
       the DISAGREE/both-allowed case does NOT qualify M3, confirmed with
       the user explicitly, even though it's permissive enough for M5), and
    2. M5's OWN confirmed ATR state (1 strong/up, -1 weak/down, from the
       same persisted tracker TM already reads for its M5 flip-exit/
       square-off) also matches that direction.
  So M3 needs M5-state + M15-structure + M15-CISD + M3-CISD all pointing the
  same way -- a much higher-conviction, all-timeframes-aligned bar than M5's
  own entry, which only ever checks the M15 gate.

  ELIGIBILITY: "one trade per m5 flip or cisd change" -> each M5 CISD is
  its own event and gets at most one trade. TrendEligibilityStore
  remembers the last M5 CISD event a trade was taken (or knowingly
  skipped as redundant) for, persisted so a restart can't re-fire the same
  event -- and since fresh_cisd() stays "fresh" for the whole bar, this is
  also what stops the same CISD firing on every poll within that bar.

  INITIAL SL: lines first, swing last -- the SAME machinery RM uses
  (sl_basis.py), but searching M5 THEN M15 and WITHOUT RM-ICT's "must be
  tighter" filter:
    1. M5's ATR-dual lines + Supertrend line that sit on the correct side
       of the live entry price (below for a BUY, above for a SELL); if
       several are usable, the FARTHEST from entry (widest SL -- the
       user's explicit choice).
    2. If M5 has none usable, the same on M15.
    3. If neither has one, the triggering CISD's own nearest active swing
       low/high, frozen at confirmation.
    4. If there's no active swing either, NO TRADE (and the event is not
       consumed).
  Buffer is applied on top in every case.

find_signal() only detects and reports; it never touches the broker or
marks anything traded -- the caller does that once it actually acts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v7_sentinel import cisd_bridge, sl_basis
from v7_sentinel.flip_state import EventType, FlipEvent, FlipStateResult
from v7_sentinel.sideways_trapper import SidewaysTrapper
from v7_sentinel.trend_bias import M15Gate

# Which timeframes' lines may supply the initial SL, in order, per
# execution timeframe -- trigger's own timeframe first, then the next
# higher one(s), same farthest-usable-line search sl_basis.py always uses.
_SL_LINE_TIMEFRAMES: dict[int, tuple[int, ...]] = {
    5: (5, 15),
    3: (3, 5, 15),   # M3 added 2026-09-22 -- assumption: extends M5's own (5, 15) chain by one step
}

# M3's own extra gate (2026-09-22): the M15 gate must be in STRICT agreement
# with the CISD's direction (not merely "allowed"), same convention as
# elsewhere in this file for "which timeframes need a tighter rule."
_STRICT_GATE_TIMEFRAMES = frozenset({3})

# Sideways Trapper applies to BOTH M5 and M3 (user, 2026-09-22: "sideways trap
# to be followed by m3 as well", then confirmed "in both m3 and m5") -- only
# ever consulted right after a genuine SL hit has been recorded for that
# direction; with nothing recorded (or once it's cleared/beaten) trading
# proceeds completely normally on both timeframes. Same
# sideways_trapper.SidewaysTrapper class RM-STR uses, its own separate
# instance/state file (trend_main.py builds it).
_TRAPPED_TIMEFRAMES = frozenset({5, 3})


@dataclass(frozen=True)
class TrendSignal:
    direction: int               # 1 buy, -1 sell
    timeframe_minutes: int         # the execution timeframe whose CISD fired
    trigger: str                    # "M5CD"
    event_time: int                  # that CISD's confirmation bar_time -- the eligibility key
    sl: float
    sl_source: str                    # "M5/ATR2", "M15/ST", "SWING", ...
    m15_structure: int                 # 1 strong/up, -1 weak/down -- the gate's own inputs, for logging
    m15_cisd: int                       # 1 bullish, -1 bearish


class TrendEligibilityStore:
    """Persists, per execution timeframe, the newest CISD event_time a
    trade has already been handled for -- see module docstring. One
    instance per symbol, each with its own state file."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._last: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._last = {str(k): int(v) for k, v in json.loads(self._path.read_text()).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._last = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._last))

    def is_traded(self, tf_minutes: int, event_time: int) -> bool:
        last = self._last.get(str(tf_minutes))
        return last is not None and event_time <= last

    def mark_traded(self, tf_minutes: int, event_time: int) -> None:
        key = str(tf_minutes)
        if self._last.get(key) is None or event_time > self._last[key]:
            self._last[key] = event_time
            self._save()


def find_flip_exit(position_direction: int, position_open_time: int, timeframe: int,
                   state_result: Optional[FlipStateResult]) -> Optional[FlipEvent]:
    """M5 FLIP EXIT (user, 2026-09-20: "the M5 flip itself closes the SELL
    straight away"): the timeframe's ATR-dual state genuinely FLIPPED against
    the open trade -- to strong under a SELL, to weak under a BUY -- on a bar
    that closed AFTER the trade was opened. Everything is decided on closed
    candles (the tracker is bar-close-gated); live price never matters.

    Deliberately an EVENT, not a state comparison: a BUY entered while M5 is
    already weak (M15 favours, "price under both lines") has a state that
    disagrees with it from the first second, and must not be closed for that
    -- only a flip that happens after entry counts. A TRAP_RESOLVED (snap-back
    to the side it was already on) is not a flip and closes nothing.
    position_open_time and the bar times share MT5's server-time base
    (verified against real deals 2026-09-20). A flip is confirmed at its bar's
    CLOSE, i.e. bar_time + the timeframe's length. Returns the event or None."""
    event = state_result.last_event if state_result is not None else None
    if event is None or event.event_type != EventType.FLIP:
        return None
    if event.confirmed.value != -position_direction:
        return None
    if event.bar_time + timeframe * 60 <= position_open_time:
        return None
    return event


def find_squareoff(symbol: str, position_direction: int, timeframe: int,
                   state: Optional[int]) -> Optional[object]:
    """SQUARE-OFF (user, 2026-09-20): an open trade is squared off by a fresh
    CISD on `timeframe` in the OPPOSITE direction that qualifies on its OWN --
    i.e. the timeframe's ATR-dual state (`state`: 1 strong, -1 weak, None
    unknown) already agrees with that CISD. Example: a SELL was entered on
    M15 bearish + an M5 bearish CISD while M5 price was above both ATR lines
    (M5 strong); an M5 BULLISH CISD now arrives with M5 still strong -- it
    qualifies a buy by itself ("price is already above atr lines"), so the SELL
    is squared off. If the state does NOT agree with the CISD (price weak,
    bullish CISD) it qualifies nothing and the trade stays open until the
    structure itself shifts. "Strong/weak" is always the CONFIRMED state as of
    the last closed candle (user, 2026-09-20: "candle close always, nothing to
    do with live price on or above the levels"), even while price sits between
    the two lines. The M15 bias is not consulted -- so no buy
    follows unless find_signal() separately allows one. Returns the CISD (for
    logging) or None."""
    if state is None:
        return None
    cisd = cisd_bridge.fresh_cisd(symbol, timeframe)
    if cisd is None:
        return None
    direction = cisd_bridge.direction_of(cisd)
    if direction != -position_direction or state != direction:
        return None
    return cisd


def find_signal(symbol: str, gate: M15Gate, execution_timeframes: tuple[int, ...],
                eligibility: TrendEligibilityStore, sl_buffer: float,
                bid: float, ask: float, m5_state: Optional[int] = None,
                trapper: Optional[SidewaysTrapper] = None, trap_min_distance: Optional[float] = None,
                ) -> Optional[TrendSignal]:
    """The first execution timeframe with a fresh, untraded CISD whose
    direction is in the M15 gate's allowed set, AND a usable initial SL --
    None otherwise. `direction` is simply the CISD's own direction (a BUY on
    a bullish M5 CISD, a SELL on a bearish one) -- the gate only ever
    permits or blocks it, never dictates it.

    m5_state (1 strong/up, -1 weak/down, None unknown -- the same persisted
    M5 ATR-dual confirmed state TM already tracks for its flip-exit/
    square-off) is only consulted for timeframes in _STRICT_GATE_TIMEFRAMES
    (M3): there, the M15 gate must be in STRICT agreement with the CISD's
    direction (not merely "allowed" -- see module docstring) AND m5_state
    must also match it, or that timeframe is skipped this cycle.

    trapper/trap_min_distance (sideways_trapper.SidewaysTrapper, added
    2026-09-22): consulted for _TRAPPED_TIMEFRAMES (both M5 and M3) -- a
    direction whose entry price would land too close to that direction's
    last SL-hit price is skipped, same rule RM-STR's own M5/M3/...
    scan uses, see that module's own docstring."""
    cache: dict = {}
    for tf in execution_timeframes:
        sl_timeframes = _SL_LINE_TIMEFRAMES.get(tf)
        if sl_timeframes is None:
            continue
        cisd = cisd_bridge.fresh_cisd(symbol, tf)
        if cisd is None:
            continue
        direction = cisd_bridge.direction_of(cisd)
        if direction not in gate.allowed:
            continue
        if tf in _STRICT_GATE_TIMEFRAMES:
            if gate.allowed != frozenset({direction}) or m5_state != direction:
                continue
        if eligibility.is_traded(tf, cisd.last_cisd_time):
            continue
        entry_price = ask if direction == 1 else bid
        if (tf in _TRAPPED_TIMEFRAMES and trapper is not None and trap_min_distance is not None
                and trapper.blocks(direction, entry_price, trap_min_distance, gate.structure)):
            continue
        resolved = sl_basis.initial_sl_basis(symbol, direction, entry_price, cisd, cache,
                                             timeframes=sl_timeframes)
        if resolved is None:
            continue
        basis, sl_source = resolved
        sl = basis - sl_buffer if direction == 1 else basis + sl_buffer
        return TrendSignal(direction=direction, timeframe_minutes=tf, trigger=f"M{tf}CD",
                           event_time=cisd.last_cisd_time, sl=sl, sl_source=sl_source,
                           m15_structure=gate.structure, m15_cisd=gate.cisd)
    return None
