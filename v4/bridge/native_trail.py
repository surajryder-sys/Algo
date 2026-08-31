"""Computes the dual-ATR Chandelier trail (same formula as
pine/OBD_ATR.pine's compute_trail(), and the same one the MQL5
SurajBot_ATRTrail_..._DUAL.mq5 indicator publishes) DIRECTLY from MT5's
own OHLC bar history via mt5.copy_rates_from_pos() -- NO chart or
indicator needs to be open in the terminal for the timeframe being read.
Added 2026-08-31, user's explicit request after closing the M2/M4 charts
over candle-lag concerns from running too many MT5 charts/indicators at
once ("can you extract real time trailing stop values from multiple
time frames without being charts open??").

A chart is a UI window; MT5's bar history is maintained by the terminal
independently of it, as long as the symbol is selected in Market Watch
(free -- no indicator computation, no rendering). This module reuses the
exact same reverse-engineered Wilder-ATR + ratcheted-trail formula
already verified multiple times this session against real incidents
(USOIL, USTEC, XAUUSD) -- see those investigations for the derivation.

Deliberately recomputes the FULL trail/trend series from scratch every
call rather than maintaining incremental state like
v3/tv_scraper/atr_trend_tracker.py does -- that module has to be
incremental because tv_scraper only ever gets one live Data Window
reading at a time, not full bar history. Here we always have the full
history for free via the API, so a full recompute is simpler, avoids
any persisted-state drift risk, and is naturally bar-close-gated: only
CLOSED bars (everything except the currently-forming last one) are ever
used, so "genuine bar close, not intrabar noise" falls out for free
without needing the bar-boundary tracking that fix required for
tv_scraper's incremental design.

Returns the SAME ATRDualSnapshot/ATRLine shape as
v4.bridge.reader.read_atr_dual() -- a drop-in equivalent, not a new
schema -- so callers (and the verification comparison script) can treat
either source interchangeably.
"""
from __future__ import annotations

import time
from typing import Optional

import MetaTrader5 as mt5

from v4.bridge.reader import ATRDualSnapshot, ATRLine

# MT5 timeframe constants keyed by minutes -- extend as needed.
_TIMEFRAME_CONST = {
    1: mt5.TIMEFRAME_M1,
    2: mt5.TIMEFRAME_M2,
    3: mt5.TIMEFRAME_M3,
    4: mt5.TIMEFRAME_M4,
    5: mt5.TIMEFRAME_M5,
    15: mt5.TIMEFRAME_M15,
    30: mt5.TIMEFRAME_M30,
    60: mt5.TIMEFRAME_H1,
    240: mt5.TIMEFRAME_H4,
}

# Generous warm-up for the slow line's default ATR period (300) plus
# margin -- matches the bar counts used throughout this session's own
# verification work.
DEFAULT_BAR_COUNT = 1500


def _wilder_atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[Optional[float]]:
    """Wilder's RMA-smoothed ATR, seeded with a plain SMA of the first
    `period` true ranges -- matches Pine's ta.atr() exactly, verified
    against real TradingView-plotted values multiple times this session."""
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
    as OBD_ATR.pine's compute_trail(), reimplemented and verified against
    real MT5/TradingView data multiple times this session (USOIL, USTEC,
    XAUUSD investigations)."""
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
    """(trend, event_time) as of the LAST bar -- event_time is the bar
    time of the most recent genuine trend CHANGE, or the first bar with
    a computed trend at all if it's never flipped within the fetched
    window (same "first bar defaults to bullish, no prior flip to point
    to" convention compute_trail() itself uses). None if no bar in the
    series has a computed trend yet (ATR warm-up not satisfied)."""
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


def read_native_trail_dual(
    symbol: str,
    tf_minutes: int,
    keyvalue_1: float = 2.0,
    atrperiod_1: int = 2,
    keyvalue_2: float = 2.0,
    atrperiod_2: int = 300,
    bar_count: int = DEFAULT_BAR_COUNT,
) -> Optional[ATRDualSnapshot]:
    """Drop-in equivalent of v4.bridge.reader.read_atr_dual(symbol, tf),
    computed entirely from MT5's own bar history instead of an MQL5
    indicator's published bridge file -- NO chart/indicator required for
    this timeframe. Defaults (keyvalue=2/atrperiod=2 and
    keyvalue=2/atrperiod=300) match OBD_ATR.pine's own defaults, which
    is what every existing MT5-native and TradingView-scraped source in
    this repo already uses.

    Returns None if the timeframe isn't recognized, MT5 doesn't return
    enough bars to satisfy the slow line's ATR warm-up, or the terminal
    connection itself has no data for this symbol yet (same
    fails-closed contract as read_atr_dual for a missing/stale bridge
    file)."""
    tf_const = _TIMEFRAME_CONST.get(tf_minutes)
    if tf_const is None:
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, bar_count)
    if rates is None or len(rates) < atrperiod_2 + 2:
        return None

    # Drop the still-forming last bar -- closed bars only, which is what
    # makes this naturally bar-close-gated (see module docstring).
    closed = rates[:-1]
    if len(closed) < atrperiod_2 + 1:
        return None

    closes = [float(r["close"]) for r in closed]
    highs = [float(r["high"]) for r in closed]
    lows = [float(r["low"]) for r in closed]
    times = [int(r["time"]) for r in closed]

    trail1, trend1 = _compute_trail_series(closes, highs, lows, keyvalue_1, atrperiod_1)
    trail2, trend2 = _compute_trail_series(closes, highs, lows, keyvalue_2, atrperiod_2)

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
