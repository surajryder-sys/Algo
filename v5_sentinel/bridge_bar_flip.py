"""Bar-close-gated flip/trap state machine sourced from the LIVE
BRIDGE's line VALUES, not copy_rates' own computed trail lines -- Trend
Manager's bridge-only redesign, confirmed 2026-09-07 ("strictly on bar
close even on bridge data"): keeps the exact same "only ever act on a
closed bar" philosophy the rest of this system already uses, but the
trail line VALUES tested against each new closed bar's close now come
from the bridge, not an independent copy_rates recompute -- removing the
drift-prone recompute (see flip_state.py's own docstring for why
independent recomputes can diverge from the live chart) as a dependency
entirely for M3/M5/M15's own state.

copy_rates is still used for ONE thing only: knowing WHEN a bar closed
and what its close price was (mt5.copy_rates_from_pos, no ATR/trail
computation of our own at all -- see read_last_closed_bar()) -- the
bridge has no bar-boundary signal, only a continuously-republished live
snapshot, so bar timing has to come from somewhere with real OHLC
history. Everything about WHICH DIRECTION that bar confirms comes from
the bridge's raw line1/line2 trail_stop values, read fresh at the moment
that bar's close is detected.

Same transition rules as flip_state.py (confirmed/watching/FLIP/
TRAP_RESOLVED -- see that module's own docstring for the full
description), just driven bar-by-bar from PERSISTED state instead of
walked over a full historical array (the bridge only ever has "now",
never history).

If the bridge has nothing to offer at the moment a new bar closes
(missing/stale), that bar is simply skipped entirely -- no update, no
guess -- and bridge_flip.StaleAlertTracker (reused here) is meant to be
wired in by the caller to alert once that's sustained past the
threshold.

FlipStateResult (from flip_state.py) is reused as the OUTPUT shape, so
every caller (structure.py, main.py, reversal_entry.py, reversal_ict.py)
keeps working against the exact same type -- only WHERE that value comes
from has changed.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5

from v5_sentinel import bridge
from v5_sentinel.flip_state import Confirmed, EventType, FlipEvent, FlipStateResult, far_near_line
from v5_sentinel.rates import _TIMEFRAME_CONST


def read_last_closed_bar(symbol: str, tf_minutes: int) -> Optional[tuple[int, float]]:
    """(bar_time, close) of the latest CLOSED bar -- no trail/ATR
    computation at all, just raw OHLC timing. None if the timeframe is
    unrecognized or there's no bar history yet."""
    tf_const = _TIMEFRAME_CONST.get(tf_minutes)
    if tf_const is None:
        return None
    raw = mt5.copy_rates_from_pos(symbol, tf_const, 0, 2)
    if raw is None or len(raw) < 2:
        return None
    closed = raw[-2]  # raw[-1] is the still-forming bar
    return int(closed["time"]), float(closed["close"])


@dataclass
class _PersistedState:
    confirmed: int                          # 1 or -1
    confirmed_since_time: int
    watching: Optional[int] = None           # 1, -1, or None
    watching_since_time: Optional[int] = None
    last_event_bar_time: Optional[int] = None
    last_event_type: Optional[str] = None     # "FLIP" / "TRAP_RESOLVED"
    last_event_confirmed: Optional[int] = None
    last_bar_time_seen: Optional[int] = None
    # The bridge's own far/near line values AT THE EXACT MOMENT the last
    # event fired -- frozen, never touched again until the NEXT event.
    # Added 2026-09-09 for Reversal Manager's own entry SL basis: "M3
    # flip candle trailing stop with buffer... decision and execution
    # analysis is based on m3 which is bar close analysis" -- deliberately
    # SEPARATE from FlipStateResult.far_line/near_line (_to_result()
    # below), which stay a live re-read on every call on purpose (SL
    # Manager's ongoing post-breakeven trailing needs that to keep
    # moving, not freeze). Use BridgeBarFlipTracker.event_far_near() to
    # read these.
    event_far: Optional[float] = None
    event_near: Optional[float] = None


class BridgeBarFlipTracker:
    """Persists one state machine per timeframe (keyed by tf_minutes --
    this file is only ever used for one symbol's own bots today, same
    convention as every other state file in this project)."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[int, _PersistedState] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._state = {int(k): _PersistedState(**v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._state = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({str(k): asdict(v) for k, v in self._state.items()}))

    def update(self, symbol: str, tf_minutes: int) -> Optional[FlipStateResult]:
        """Call every cycle. Only actually advances the state machine
        when a NEW closed bar is seen (by bar_time) AND the bridge has
        fresh data at that exact moment -- otherwise returns the
        CURRENT (unchanged) state, same "nothing new happened this
        cycle" contract every FlipStateResult caller already expects
        (event_just_happened() stays True for the whole window a bar
        remains the latest closed one, exactly like flip_state.compute()
        -- existing bar-time dedup, e.g. main.py's RuntimeState, keeps
        working unchanged). None only if there's no baseline at all yet
        (bar data itself unavailable, or the very first bar's bridge
        data was never available either)."""
        bar = read_last_closed_bar(symbol, tf_minutes)
        if bar is None:
            return None
        bar_time, close = bar

        state = self._state.get(tf_minutes)

        if state is None or state.last_bar_time_seen != bar_time:
            lines = bridge.read_lines(symbol, tf_minutes)
            if lines is not None:
                lo, hi = min(lines), max(lines)
                state = self._advance(state, close, lo, hi, bar_time)
                self._state[tf_minutes] = state
                self._save()
            # else: new bar closed but bridge has nothing right now --
            # skip this bar entirely (state, if any, stays as it was).

        if state is None:
            return None
        return self._to_result(symbol, tf_minutes, state, close, bar_time)

    def _advance(self, state: Optional[_PersistedState], close: float, lo: float, hi: float,
                bar_time: int) -> _PersistedState:
        """Single-step transition -- mirrors flip_state.compute()'s own
        per-bar loop body exactly, just applied once against persisted
        state instead of walked over a full array."""
        if state is None:
            confirmed = 1 if close > hi else (-1 if close < lo else 1)  # bootstrap, same convention as flip_state
            return _PersistedState(confirmed=confirmed, confirmed_since_time=bar_time, last_bar_time_seen=bar_time)

        confirmed, watching, watching_since = state.confirmed, state.watching, state.watching_since_time
        confirmed_since = state.confirmed_since_time
        ev_time, ev_type, ev_dir = state.last_event_bar_time, state.last_event_type, state.last_event_confirmed
        prior_event_bar_time = state.last_event_bar_time  # to detect a genuinely NEW event below

        if watching is None:
            if confirmed == 1:
                if close < lo:
                    confirmed, confirmed_since = -1, bar_time
                    ev_time, ev_type, ev_dir = bar_time, "FLIP", -1
                elif not (close > hi):
                    watching, watching_since = -1, bar_time
            else:
                if close > hi:
                    confirmed, confirmed_since = 1, bar_time
                    ev_time, ev_type, ev_dir = bar_time, "FLIP", 1
                elif not (close < lo):
                    watching, watching_since = 1, bar_time
        else:
            if watching == 1:  # came from BEAR, watching for a bullish flip
                if close > hi:
                    confirmed, confirmed_since = 1, bar_time
                    ev_time, ev_type, ev_dir = bar_time, "FLIP", 1
                    watching, watching_since = None, None
                elif close < lo:
                    confirmed_since = bar_time
                    ev_time, ev_type, ev_dir = bar_time, "TRAP_RESOLVED", -1
                    watching, watching_since = None, None
            else:  # watching == -1, came from BULL
                if close < lo:
                    confirmed, confirmed_since = -1, bar_time
                    ev_time, ev_type, ev_dir = bar_time, "FLIP", -1
                    watching, watching_since = None, None
                elif close > hi:
                    confirmed_since = bar_time
                    ev_time, ev_type, ev_dir = bar_time, "TRAP_RESOLVED", 1
                    watching, watching_since = None, None

        if ev_time != prior_event_bar_time and ev_dir is not None:
            # A genuinely NEW event just fired this call -- freeze far/near
            # at exactly this moment (lo/hi are the bridge's own values as
            # of THIS bar, never re-read for this event again).
            event_far, event_near = far_near_line(ev_dir, lo, hi)
        else:
            event_far, event_near = state.event_far, state.event_near

        return _PersistedState(confirmed=confirmed, confirmed_since_time=confirmed_since, watching=watching,
                               watching_since_time=watching_since, last_event_bar_time=ev_time,
                               last_event_type=ev_type, last_event_confirmed=ev_dir, last_bar_time_seen=bar_time,
                               event_far=event_far, event_near=event_near)

    def _to_result(self, symbol: str, tf_minutes: int, state: _PersistedState, last_close: float,
                   last_time: int) -> FlipStateResult:
        confirmed_enum = Confirmed.BULL if state.confirmed == 1 else Confirmed.BEAR
        watching_enum = None if state.watching is None else (Confirmed.BULL if state.watching == 1 else Confirmed.BEAR)
        last_event = None
        if state.last_event_bar_time is not None:
            last_event = FlipEvent(
                bar_index=-1, bar_time=state.last_event_bar_time,
                event_type=EventType.FLIP if state.last_event_type == "FLIP" else EventType.TRAP_RESOLVED,
                confirmed=Confirmed.BULL if state.last_event_confirmed == 1 else Confirmed.BEAR,
            )
        # far/near read fresh (not frozen from the bar-close moment) --
        # matches how SL Manager/watch-zone already treat trailing lines
        # as continuously moving, not bar-locked.
        lines = bridge.read_lines(symbol, tf_minutes)
        if lines is not None:
            far, near = far_near_line(state.confirmed, lines[0], lines[1])
        else:
            far, near = last_close, last_close  # nothing fresher available -- display-only fallback

        return FlipStateResult(
            symbol=symbol, timeframe_minutes=tf_minutes, confirmed=confirmed_enum,
            confirmed_since_time=state.confirmed_since_time, watching=watching_enum,
            watching_since_time=state.watching_since_time, last_event=last_event,
            far_line=far, near_line=near, last_close=last_close, last_time=last_time,
        )

    def event_far_near(self, tf_minutes: int) -> Optional[tuple[float, float]]:
        """(far, near) as of the EXACT bar that produced this timeframe's
        last event (FLIP/TRAP_RESOLVED) -- frozen at that moment, unlike
        FlipStateResult.far_line/near_line above (see _PersistedState's
        own docstring for why the two are kept deliberately separate).
        None if update() hasn't been called for this timeframe yet, or no
        event has ever fired for it."""
        state = self._state.get(tf_minutes)
        if state is None or state.event_far is None or state.event_near is None:
            return None
        return state.event_far, state.event_near
