"""Fast/slow moving-average crossover signal, evaluated on closed bars only."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import MetaTrader5 as mt5
import pandas as pd

from mt5_ma_bot.config import Config
from mt5_ma_bot.connection import resolve_timeframe

log = logging.getLogger(__name__)


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


@dataclass(frozen=True)
class SignalResult:
    signal: Signal
    bar_time: pd.Timestamp
    fast_ma: float
    slow_ma: float
    close: float


def _moving_average(series: pd.Series, period: int, method: str) -> pd.Series:
    if method == "SMA":
        return series.rolling(window=period).mean()
    if method == "EMA":
        return series.ewm(span=period, adjust=False).mean()
    if method == "SMMA":
        return series.ewm(alpha=1.0 / period, adjust=False).mean()
    if method == "LWMA":
        weights = pd.Series(range(1, period + 1), dtype=float)
        return series.rolling(window=period).apply(
            lambda w: (w * weights.values).sum() / weights.sum(), raw=True
        )
    raise ValueError(f"Unsupported MA method: {method}")


def fetch_closed_bars(symbol: str, timeframe_name: str, count: int) -> pd.DataFrame:
    """Returns the `count` most recent CLOSED bars (excludes the still-forming bar)."""
    timeframe = resolve_timeframe(timeframe_name)
    # start=1 skips index 0, which is the currently forming (incomplete) bar.
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)
    if rates is None or len(rates) < count:
        code, desc = mt5.last_error()
        raise RuntimeError(f"Failed to fetch rates for {symbol} {timeframe_name}: [{code}] {desc}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def compute_signal(cfg: Config) -> SignalResult:
    needed = cfg.slow_ma_period + 3
    df = fetch_closed_bars(cfg.symbol, cfg.timeframe_name, needed)

    df["fast_ma"] = _moving_average(df["close"], cfg.fast_ma_period, cfg.ma_method)
    df["slow_ma"] = _moving_average(df["close"], cfg.slow_ma_period, cfg.ma_method)
    df = df.dropna(subset=["fast_ma", "slow_ma"]).reset_index(drop=True)

    if len(df) < 2:
        raise RuntimeError("Not enough bars to evaluate a crossover; increase history or reduce SLOW_MA_PERIOD")

    prev, last = df.iloc[-2], df.iloc[-1]
    prev_diff = prev["fast_ma"] - prev["slow_ma"]
    last_diff = last["fast_ma"] - last["slow_ma"]

    if prev_diff <= 0 < last_diff:
        signal = Signal.BUY
    elif prev_diff >= 0 > last_diff:
        signal = Signal.SELL
    else:
        signal = Signal.NONE

    return SignalResult(
        signal=signal,
        bar_time=last["time"],
        fast_ma=float(last["fast_ma"]),
        slow_ma=float(last["slow_ma"]),
        close=float(last["close"]),
    )
