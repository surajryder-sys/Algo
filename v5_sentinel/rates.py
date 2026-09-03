"""ATR Trail (dual-line) + Major/Minor swing structure, both computed
DIRECTLY from MT5's own OHLC bar history via mt5.copy_rates_from_pos() --
NO chart or indicator needs to be open in the terminal for any of the
timeframes read here. Self-contained: no bridge JSON file, no MQL5
indicator, and (per this repo's per-lineage isolation convention) no
import from algo_v2/v3/v4 -- this is V5-Sentinel's own copy.

Same "MetaRates" approach v4/bridge/native_trail.py already proved out for
ATR alone (2026-08-31, user's explicit request after closing extra charts
over candle-lag concerns -- see that file's docstring for the ATR-side
derivation history). This module ports that same technique to Major/Minor
structure too, and covers all 8 timeframes requested: H4, H2, H1, M30,
M15, M5, M3, M1.

IMPORTANT ASYMMETRY between the two calculations, worth understanding
before trusting Major/Minor levels the way ATR's are trusted:

  ATR Trail's Wilder smoothing is self-correcting -- start it from ANY
  reasonable point in history with enough warm-up bars and it converges
  to the same value within a handful of bars, regardless of exactly where
  the fetched window began. That's why a fixed bar_count works fine there.

  Major/Minor's ZigZag + Major/Minor promotion is NOT self-correcting --
  it's a genuine running history (which pivot became "Major" depends on
  every promotion since the sequence began). A rolling window that starts
  mid-history seeds its own Major High/Low from whatever the first two
  pivots inside that window happen to be, which is NOT guaranteed to
  match what a chart with the indicator attached since forever would show
  right now. Bigger bar_count narrows this gap but never eliminates it
  the way ATR's warm-up does. Treat Major/Minor levels from this module
  as "structure over the fetched window", not "the chart's true
  since-inception Major/Minor" -- fine for a bias input, not something to
  assume is identical to MajorMinor_Secret_ShortTerm.mq5's own chart
  output unless bar_count covers that chart's full visible history too.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

# MT5 timeframe constants -- covers the 8 timeframes actually requested
# (H4,H2,H1,M30,M15,M5,M3,M1) plus a couple of common neighbors for free.
_TIMEFRAME_CONST = {
    1: mt5.TIMEFRAME_M1,
    3: mt5.TIMEFRAME_M3,
    5: mt5.TIMEFRAME_M5,
    15: mt5.TIMEFRAME_M15,
    30: mt5.TIMEFRAME_M30,
    60: mt5.TIMEFRAME_H1,
    120: mt5.TIMEFRAME_H2,
    240: mt5.TIMEFRAME_H4,
}

# H4, H2, H1, M30, M15, M5, M3, M1 -- the exact set + order requested.
TARGET_TIMEFRAMES_MINUTES = [240, 120, 60, 30, 15, 5, 3, 1]

# Generous warm-up for the slow ATR line's default period (300) plus
# margin -- matches v4/bridge/native_trail.py's own default.
DEFAULT_ATR_BAR_COUNT = 1500

# Major/Minor has no fixed warm-up requirement the way ATR does (see module
# docstring's asymmetry warning) -- this is just "as much history as
# practical" per timeframe. Tune per-timeframe via bar_count if needed.
DEFAULT_MM_BAR_COUNT = 3000


# ===================== ATR Trail (dual-line) =====================

@dataclass(frozen=True)
class ATRLine:
    trail_stop: float
    trend: int              # 1 bullish, -1 bearish
    event_time: int         # bar time of this line's own last trend flip


@dataclass(frozen=True)
class ATRDualSnapshot:
    symbol: str
    timeframe_minutes: int
    updated: int
    line1: ATRLine
    line2: ATRLine
    structure: str                 # "STRONG" / "WEAK" / "UNDECISIVE"
    structure_event_time: int


def _wilder_atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[Optional[float]]:
    """Wilder's RMA-smoothed ATR, seeded with a plain SMA of the first
    `period` true ranges -- matches Pine's ta.atr() exactly."""
    trs = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)

    atr: list[Optional[float]] = [None] * len(trs)
    if len(trs) < period:
        return atr
    seed = sum(trs[:period]) / period
    atr[period - 1] = seed
    for i in range(period, len(trs)):
        atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period
    return atr


def _compute_trail_series(
    closes: list[float], highs: list[float], lows: list[float], keyvalue: float, atrperiod: int,
) -> tuple[list[Optional[float]], list[Optional[int]]]:
    """(trail, trend) per bar -- the same ratcheted Chandelier-exit logic
    every ATR Trail indicator in this repo uses."""
    atr = _wilder_atr(highs, lows, closes, atrperiod)
    trail: list[Optional[float]] = [None] * len(closes)
    trend: list[Optional[int]] = [None] * len(closes)

    for i in range(len(closes)):
        if atr[i] is None:
            continue
        n_loss = keyvalue * atr[i]
        src = closes[i]

        if trail[i - 1] is None if i > 0 else True:
            trail[i] = src - n_loss
            trend[i] = 1
            continue

        prev_trail = trail[i - 1]
        prev_src = closes[i - 1]
        if src > prev_trail and prev_src > prev_trail:
            trail[i] = max(prev_trail, src - n_loss)
        elif src < prev_trail and prev_src < prev_trail:
            trail[i] = min(prev_trail, src + n_loss)
        elif src > prev_trail:
            trail[i] = src - n_loss
        else:
            trail[i] = src + n_loss

        prev_trend = trend[i - 1]
        if prev_src < prev_trail and src > trail[i]:
            trend[i] = 1
        elif prev_src > prev_trail and src < trail[i]:
            trend[i] = -1
        else:
            trend[i] = prev_trend

    return trail, trend


def _last_flip(trend: list[Optional[int]], times: list[int]) -> Optional[tuple[int, int]]:
    """(trend, event_time) as of the last bar -- event_time is the bar time
    of the most recent genuine trend CHANGE."""
    last_valid = None
    for i in range(len(trend) - 1, -1, -1):
        if trend[i] is not None:
            last_valid = i
            break
    if last_valid is None:
        return None

    current_trend = trend[last_valid]
    for i in range(last_valid, 0, -1):
        if trend[i - 1] is None:
            return current_trend, times[i]
        if trend[i] != trend[i - 1]:
            return current_trend, times[i]
    return current_trend, times[0]


@dataclass(frozen=True)
class TrailSeries:
    """Full closed-bar series backing an ATRDualSnapshot -- exposed
    separately because the flip/trap state machine (flip_state.py) needs
    to walk bar-by-bar through close vs. both trail lines, not just read
    the latest values."""
    symbol: str
    timeframe_minutes: int
    times: list[int]
    closes: list[float]
    trail1: list[Optional[float]]
    trail2: list[Optional[float]]
    trend1: list[Optional[int]]
    trend2: list[Optional[int]]


def read_trail_series(
    symbol: str,
    tf_minutes: int,
    keyvalue_1: float = 2.0,
    atrperiod_1: int = 2,
    keyvalue_2: float = 2.0,
    atrperiod_2: int = 300,
    bar_count: int = DEFAULT_ATR_BAR_COUNT,
) -> Optional[TrailSeries]:
    """Closed-bar close/trail1/trail2 arrays for one timeframe, computed
    entirely from MT5's own bar history -- no chart/indicator required.
    Returns None if the timeframe isn't recognized or there isn't enough
    bar history to satisfy the slow line's ATR warm-up."""
    tf_const = _TIMEFRAME_CONST.get(tf_minutes)
    if tf_const is None:
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, bar_count)
    if rates is None or len(rates) < atrperiod_2 + 2:
        return None

    # Drop the still-forming last bar -- closed bars only.
    closed = rates[:-1]
    if len(closed) < atrperiod_2 + 1:
        return None

    closes = [float(r["close"]) for r in closed]
    highs = [float(r["high"]) for r in closed]
    lows = [float(r["low"]) for r in closed]
    times = [int(r["time"]) for r in closed]

    trail1, trend1 = _compute_trail_series(closes, highs, lows, keyvalue_1, atrperiod_1)
    trail2, trend2 = _compute_trail_series(closes, highs, lows, keyvalue_2, atrperiod_2)

    return TrailSeries(symbol=symbol, timeframe_minutes=tf_minutes, times=times, closes=closes,
                        trail1=trail1, trail2=trail2, trend1=trend1, trend2=trend2)


def read_atr_dual(
    symbol: str,
    tf_minutes: int,
    keyvalue_1: float = 2.0,
    atrperiod_1: int = 2,
    keyvalue_2: float = 2.0,
    atrperiod_2: int = 300,
    bar_count: int = DEFAULT_ATR_BAR_COUNT,
) -> Optional[ATRDualSnapshot]:
    """ATR Trail dual-line snapshot for one timeframe, computed entirely
    from MT5's own bar history -- no chart/indicator required. Returns
    None if the timeframe isn't recognized or there isn't enough bar
    history to satisfy the slow line's ATR warm-up."""
    series = read_trail_series(symbol, tf_minutes, keyvalue_1, atrperiod_1, keyvalue_2, atrperiod_2, bar_count)
    if series is None:
        return None

    times = series.times
    trail1, trail2 = series.trail1, series.trail2
    trend1, trend2 = series.trend1, series.trend2

    flip1 = _last_flip(trend1, times)
    flip2 = _last_flip(trend2, times)
    if flip1 is None or flip2 is None or trail1[-1] is None or trail2[-1] is None:
        return None

    t1, et1 = flip1
    t2, et2 = flip2

    if t1 == 1 and t2 == 1:
        structure = "STRONG"
    elif t1 == -1 and t2 == -1:
        structure = "WEAK"
    else:
        structure = "UNDECISIVE"

    return ATRDualSnapshot(
        symbol=symbol,
        timeframe_minutes=tf_minutes,
        updated=int(time.time()),
        line1=ATRLine(trail_stop=trail1[-1], trend=t1, event_time=et1),
        line2=ATRLine(trail_stop=trail2[-1], trend=t2, event_time=et2),
        structure=structure,
        structure_event_time=max(et1, et2),
    )


def read_all_atr_dual(symbol: str, **kwargs) -> dict[int, Optional[ATRDualSnapshot]]:
    """ATR Trail dual-line snapshot for all 8 target timeframes at once,
    keyed by timeframe_minutes. A None value for a timeframe means that
    one didn't have enough bar history -- other timeframes are unaffected."""
    return {tf: read_atr_dual(symbol, tf, **kwargs) for tf in TARGET_TIMEFRAMES_MINUTES}


# ===================== Major/Minor swing structure =====================

@dataclass(frozen=True)
class MajorMinorLevel:
    value: float
    time: int   # bar time of the pivot this level is anchored to


@dataclass(frozen=True)
class MajorMinorSnapshot:
    symbol: str
    timeframe_minutes: int
    updated: int
    major_support: Optional[MajorMinorLevel]
    major_resistance: Optional[MajorMinorLevel]
    minor_support: Optional[MajorMinorLevel]
    minor_resistance: Optional[MajorMinorLevel]


class _MMState:
    """Scratch state for one full pass over a bar range -- direct port of
    mql5/MajorMinor_Secret_ShortTerm.mq5's confirmed-state variables
    (the gc_* ones there). No separate "working" copy is needed here the
    way the MQL5 version needs one: that split exists purely to let MT5
    reprocess the still-forming bar every tick without corrupting already-
    confirmed history. This module always runs once over a static array of
    already-closed bars, so there's no forming bar to protect against --
    a single straight pass through does the same job."""

    def __init__(self):
        self.type: list[str] = []
        self.value: list[float] = []
        self.index: list[int] = []
        self.type_adv: list[str] = []
        self.value_adv: list[float] = []
        self.index_adv: list[int] = []
        self.major_high_level = 0.0
        self.major_low_level = 0.0
        self.major_levels_set = False
        self.lock0 = True
        self.lock1 = True
        self.last_high_value = 0.0
        self.last_high_index = -1
        self.last_low_value = 0.0
        self.last_low_index = -1
        self.maj_sup: Optional[tuple[int, float]] = None
        self.maj_res: Optional[tuple[int, float]] = None
        self.min_sup: Optional[tuple[int, float]] = None
        self.min_res: Optional[tuple[int, float]] = None


def _is_pivot_high(highs: list[float], c: int, pp: int, total: int) -> bool:
    if c - pp < 0 or c + pp >= total:
        return False
    v = highs[c]
    for k in range(c - pp, c + pp + 1):
        if k != c and highs[k] >= v:
            return False
    return True


def _is_pivot_low(lows: list[float], c: int, pp: int, total: int) -> bool:
    if c - pp < 0 or c + pp >= total:
        return False
    v = lows[c]
    for k in range(c - pp, c + pp + 1):
        if k != c and lows[k] <= v:
            return False
    return True


def _push_high_type(s: _MMState) -> None:
    n = len(s.type)  # size BEFORE this push
    t = ("HH" if s.value[n - 2] < s.last_high_value else "LH") if n > 2 else "H"
    s.type.append(t); s.value.append(s.last_high_value); s.index.append(s.last_high_index)


def _push_low_type(s: _MMState) -> None:
    n = len(s.type)
    t = ("HL" if s.value[n - 2] < s.last_low_value else "LL") if n > 2 else "L"
    s.type.append(t); s.value.append(s.last_low_value); s.index.append(s.last_low_index)


def _replace_last_with_high_type(s: _MMState) -> None:
    s.type.pop(); s.value.pop(); s.index.pop()
    _push_high_type(s)


def _replace_last_with_low_type(s: _MMState) -> None:
    s.type.pop(); s.value.pop(); s.index.pop()
    _push_low_type(s)


def _classify_pivot(s: _MMState, has_high: bool, has_low: bool, this_close: float) -> None:
    """Direct port of ClassifyPivot -- base ZigZag alternating-point
    construction, mirroring the Pine source's if-bool(HighPivot)-and-
    bool(LowPivot) / else-if branches exactly."""
    n = len(s.type)

    if has_high and has_low:
        if n == 0:
            return  # both pivots land on an empty sequence -- discarded
        last = s.type[n - 1]
        if last in ("L", "LL"):
            _replace_last_with_low_type(s) if s.last_low_value < s.value[n - 1] else _push_high_type(s)
        elif last in ("H", "HH"):
            _replace_last_with_high_type(s) if s.last_high_value > s.value[n - 1] else _push_low_type(s)
        elif last == "LH":
            if s.last_high_value < s.value[n - 1]:
                _push_low_type(s)
            elif s.last_high_value > s.value[n - 1]:
                if this_close < s.value[n - 1]:
                    _replace_last_with_high_type(s)
                elif this_close > s.value[n - 1]:
                    _push_low_type(s)
        elif last == "HL":
            if s.last_low_value > s.value[n - 1]:
                _push_high_type(s)
            elif s.last_low_value < s.value[n - 1]:
                if this_close > s.value[n - 1]:
                    _replace_last_with_low_type(s)
                elif this_close < s.value[n - 1]:
                    _push_high_type(s)

    elif has_high:
        if n == 0:
            s.type.insert(0, "H"); s.value.insert(0, s.last_high_value); s.index.insert(0, s.last_high_index)
        else:
            last = s.type[n - 1]
            if last in ("L", "HL", "LL"):
                if s.last_high_value > s.value[n - 1]:
                    _push_high_type(s)
                elif s.last_high_value < s.value[n - 1]:
                    _replace_last_with_low_type(s)
            elif last in ("H", "HH", "LH"):
                if s.value[n - 1] < s.last_high_value:
                    _replace_last_with_high_type(s)

    elif has_low:
        if n == 0:
            s.type.insert(0, "L"); s.value.insert(0, s.last_low_value); s.index.insert(0, s.last_low_index)
        else:
            last = s.type[n - 1]
            if last in ("H", "HH", "LH"):
                if s.last_low_value < s.value[n - 1]:
                    _push_low_type(s)
                elif s.last_low_value > s.value[n - 1]:
                    _replace_last_with_high_type(s)
            elif last in ("L", "HL", "LL"):
                if s.value[n - 1] > s.last_low_value:
                    _replace_last_with_low_type(s)


def _update_major_levels(s: _MMState, this_close: float) -> None:
    """Direct port of UpdateMajorLevels -- break-of-structure promotion,
    reacting to CLOSE crossing the current Major High/Low level."""
    n_adv = len(s.type_adv)
    if n_adv <= 1:
        return
    n_base = len(s.type)
    if n_base < 1:
        return

    if this_close > s.major_high_level:
        t = s.type_adv[n_adv - 1]
        if t == "mL":
            s.type_adv[n_adv - 1] = "ML"
            s.major_low_level = s.value_adv[n_adv - 1]
        elif t in ("mHL", "mLL"):
            s.type_adv[n_adv - 1] = "M" + s.type[n_base - 1]
            s.major_low_level = s.value_adv[n_adv - 1]
        elif t in ("mLH", "mHH", "MLH", "MHH"):
            if n_adv >= 2 and n_base >= 2 and s.type_adv[n_adv - 2] in ("mHL", "mLL"):
                s.type_adv[n_adv - 2] = "M" + s.type[n_base - 2]
                s.major_low_level = s.value_adv[n_adv - 2]

    if s.value_adv[n_adv - 1] > s.major_high_level:
        t = s.type_adv[n_adv - 1]
        if t == "mH":
            s.type_adv[n_adv - 1] = "MH"
            s.major_high_level = s.value_adv[n_adv - 1]
        elif t in ("mLH", "mHH", "MHH"):
            s.type_adv[n_adv - 1] = "M" + s.type[n_base - 1]
            s.major_high_level = s.value_adv[n_adv - 1]

    if this_close < s.major_low_level:
        t = s.type_adv[n_adv - 1]
        if t == "mH":
            s.type_adv[n_adv - 1] = "MH"
            s.major_high_level = s.value_adv[n_adv - 1]
        elif t in ("mLH", "mHH"):
            s.type_adv[n_adv - 1] = "M" + s.type[n_base - 1]
            s.major_high_level = s.value_adv[n_adv - 1]
        elif t in ("mHL", "mLL", "MHL", "MLL"):
            if n_adv >= 2 and n_base >= 2 and s.type_adv[n_adv - 2] in ("mLH", "mHH"):
                s.type_adv[n_adv - 2] = "M" + s.type[n_base - 2]
                s.major_high_level = s.value_adv[n_adv - 2]

    if s.value_adv[n_adv - 1] < s.major_low_level:
        t = s.type_adv[n_adv - 1]
        if t == "mL":
            s.type_adv[n_adv - 1] = "ML"
            s.major_low_level = s.value_adv[n_adv - 1]
        elif t in ("mHL", "mLL", "MLL"):
            s.type_adv[n_adv - 1] = "M" + s.type[n_base - 1]
            s.major_low_level = s.value_adv[n_adv - 1]


def _process_bar(s: _MMState, i: int, pp: int, highs: list[float], lows: list[float], closes: list[float]) -> None:
    """Direct port of ProcessBar -- classifies bar i's pivot (if any) into
    the base ZigZag sequence, seeds/promotes Major levels."""
    c = i - pp
    has_high = has_low = False
    total = len(highs)
    if c >= pp and c + pp < total:
        has_high = _is_pivot_high(highs, c, pp, total)
        has_low = _is_pivot_low(lows, c, pp, total)

    if has_high:
        s.last_high_value = highs[c]; s.last_high_index = c
    if has_low:
        s.last_low_value = lows[c]; s.last_low_index = c

    this_close = closes[i]

    prev_n = len(s.value)
    prev_last_value = s.value[prev_n - 1] if prev_n > 0 else None
    prev_last_type = s.type[prev_n - 1] if prev_n > 0 else ""

    _classify_pivot(s, has_high, has_low, this_close)

    # First Major/Minor detector: seeds Major High/Low the moment the base
    # sequence first reaches 2 points.
    n = len(s.type)
    if n == 2:
        if s.type[0] == "H":
            s.major_high_level, s.major_low_level = s.value[0], s.value[1]
        elif s.type[0] == "L":
            s.major_high_level, s.major_low_level = s.value[1], s.value[0]
        s.major_levels_set = True

    # Lock0 / Lock1: seed the Adv (pending) arrays from the first two base points.
    if len(s.value) == 1 and s.lock0:
        s.type_adv.insert(0, "M" + s.type[0]); s.value_adv.insert(0, s.value[0]); s.index_adv.insert(0, s.index[0])
        s.lock0 = False
    if len(s.value) == 2 and s.lock1:
        s.type_adv.insert(1, "M" + s.type[1]); s.value_adv.insert(1, s.value[1]); s.index_adv.insert(1, s.index[1])
        s.lock1 = False

    # Adv-array sync: only reacts on the bar the base array actually changed.
    n = len(s.value)
    if n > 1:
        cur_last_value = s.value[n - 1]
        cur_last_type = s.type[n - 1]
        if prev_last_value is None or cur_last_value != prev_last_value:
            prev_family = prev_last_type[-1] if prev_last_type else ""
            cur_family = cur_last_type[-1]
            if prev_family != cur_family:
                s.type_adv.append("m" + cur_last_type); s.value_adv.append(cur_last_value); s.index_adv.append(s.index[n - 1])
            elif s.value_adv:
                s.value_adv[-1] = cur_last_value; s.index_adv[-1] = s.index[n - 1]

    if s.major_levels_set:
        _update_major_levels(s, this_close)


def _track_latest_positions(s: _MMState) -> None:
    """Direct port of TrackLatestPositions -- remembers which (index,
    value) each of the 4 line categories most recently belonged to."""
    n_adv = len(s.type_adv)
    if n_adv <= 2:
        return
    x, y, t = s.index_adv[n_adv - 1], s.value_adv[n_adv - 1], s.type_adv[n_adv - 1]

    if t in ("MLL", "MHL"):
        s.maj_sup = (x, y)
    elif t in ("MHH", "MLH"):
        s.maj_res = (x, y)
    elif t in ("mLL", "mHL"):
        s.min_sup = (x, y)
    elif t in ("mHH", "mLH"):
        s.min_res = (x, y)


def read_major_minor(
    symbol: str,
    tf_minutes: int,
    pivot_period: int = 5,
    bar_count: int = DEFAULT_MM_BAR_COUNT,
) -> Optional[MajorMinorSnapshot]:
    """Major/Minor Support/Resistance snapshot for one timeframe, computed
    entirely from MT5's own bar history -- no chart/indicator required.
    See module docstring for the ATR-vs-Major/Minor convergence caveat
    before trusting these levels the way ATR's are trusted. Returns None
    if the timeframe isn't recognized or there isn't enough bar history
    for even one confirmed pivot window."""
    tf_const = _TIMEFRAME_CONST.get(tf_minutes)
    if tf_const is None:
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, bar_count)
    if rates is None or len(rates) < 2 * pivot_period + 2:
        return None

    # Drop the still-forming last bar -- closed bars only, same contract
    # as read_atr_dual.
    closed = rates[:-1]
    if len(closed) < 2 * pivot_period + 2:
        return None

    closes = [float(r["close"]) for r in closed]
    highs = [float(r["high"]) for r in closed]
    lows = [float(r["low"]) for r in closed]
    times = [int(r["time"]) for r in closed]

    state = _MMState()
    for i in range(len(closes)):
        _process_bar(state, i, pivot_period, highs, lows, closes)
        _track_latest_positions(state)

    def _level(pos: Optional[tuple[int, float]]) -> Optional[MajorMinorLevel]:
        if pos is None:
            return None
        idx, val = pos
        return MajorMinorLevel(value=val, time=times[idx])

    return MajorMinorSnapshot(
        symbol=symbol,
        timeframe_minutes=tf_minutes,
        updated=int(time.time()),
        major_support=_level(state.maj_sup),
        major_resistance=_level(state.maj_res),
        minor_support=_level(state.min_sup),
        minor_resistance=_level(state.min_res),
    )


def read_all_major_minor(symbol: str, **kwargs) -> dict[int, Optional[MajorMinorSnapshot]]:
    """Major/Minor snapshot for all 8 target timeframes at once, keyed by
    timeframe_minutes. A None value for a timeframe means that one didn't
    have enough bar history -- other timeframes are unaffected."""
    return {tf: read_major_minor(symbol, tf, **kwargs) for tf in TARGET_TIMEFRAMES_MINUTES}
