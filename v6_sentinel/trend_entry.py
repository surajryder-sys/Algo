"""TM-STR entry engine. Confirmed with the user 2026-09-20:

  ENTRY: "buy on bullish m5 cisd - if m15 favours; sell on bearish m5
  cisd - if m15 favours." Execution timeframe = M5 (M3 to be added
  later). ONLY a fresh M5 CISD triggers an entry -- an M5 ATR flip is NOT
  a trigger (asked and answered explicitly). Uses cisd_bridge.fresh_cisd()
  (the "privileged, momentary, only non-None the EXACT bar it confirmed"
  contract), so a CISD that is merely still standing from earlier never
  fires, and the M15 bias (trend_bias.py) must currently agree with the
  CISD's direction.

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

from v6_sentinel import cisd_bridge, sl_basis
from v6_sentinel.flip_state import EventType, FlipEvent, FlipStateResult
from v6_sentinel.trend_bias import Bias

# Which timeframes' lines may supply the initial SL, in order, per
# execution timeframe. M3 gets its own entry here when it is added.
_SL_LINE_TIMEFRAMES: dict[int, tuple[int, ...]] = {
    5: (5, 15),
}


@dataclass(frozen=True)
class TrendSignal:
    direction: int               # 1 buy, -1 sell
    timeframe_minutes: int         # the execution timeframe whose CISD fired
    trigger: str                    # "M5CD"
    event_time: int                  # that CISD's confirmation bar_time -- the eligibility key
    sl: float
    sl_source: str                    # "M5/ATR2", "M15/ST", "SWING", ...
    bias_source: str                   # "ATR" | "CISD" -- what set the M15 bias
    bias_event_time: int


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


def find_signal(symbol: str, bias: Bias, execution_timeframes: tuple[int, ...],
                eligibility: TrendEligibilityStore, sl_buffer: float,
                bid: float, ask: float) -> Optional[TrendSignal]:
    """The first execution timeframe with a fresh, untraded CISD matching
    the M15 bias AND a usable initial SL -- None otherwise."""
    cache: dict = {}
    for tf in execution_timeframes:
        sl_timeframes = _SL_LINE_TIMEFRAMES.get(tf)
        if sl_timeframes is None:
            continue
        cisd = cisd_bridge.fresh_cisd(symbol, tf)
        if cisd is None or cisd_bridge.direction_of(cisd) != bias.direction:
            continue
        if eligibility.is_traded(tf, cisd.last_cisd_time):
            continue
        direction = bias.direction
        entry_price = ask if direction == 1 else bid
        resolved = sl_basis.initial_sl_basis(symbol, direction, entry_price, cisd, cache,
                                             timeframes=sl_timeframes)
        if resolved is None:
            continue
        basis, sl_source = resolved
        sl = basis - sl_buffer if direction == 1 else basis + sl_buffer
        return TrendSignal(direction=direction, timeframe_minutes=tf, trigger=f"M{tf}CD",
                           event_time=cisd.last_cisd_time, sl=sl, sl_source=sl_source,
                           bias_source=bias.source, bias_event_time=bias.event_time)
    return None
