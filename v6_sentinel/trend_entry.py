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
