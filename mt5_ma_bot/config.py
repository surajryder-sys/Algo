"""Loads bot configuration from environment variables (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

_TIMEFRAME_NAMES = (
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12",
    "D1", "W1", "MN1",
)


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Config:
    login: int
    password: str
    server: str
    terminal_path: str | None

    symbol: str
    timeframe_name: str
    fast_ma_period: int
    slow_ma_period: int
    ma_method: str

    risk_percent: float
    stop_loss_pips: float
    take_profit_pips: float
    magic_number: int
    deviation_points: int

    poll_seconds: int

    def __post_init__(self) -> None:
        if self.timeframe_name not in _TIMEFRAME_NAMES:
            raise ValueError(f"Unsupported TIMEFRAME '{self.timeframe_name}'. Valid: {_TIMEFRAME_NAMES}")
        if self.fast_ma_period >= self.slow_ma_period:
            raise ValueError("FAST_MA_PERIOD must be smaller than SLOW_MA_PERIOD")
        if self.ma_method not in ("SMA", "EMA", "SMMA", "LWMA"):
            raise ValueError(f"Unsupported MA_METHOD '{self.ma_method}'. Valid: SMA, EMA, SMMA, LWMA")
        if not (0 < self.risk_percent <= 100):
            raise ValueError("RISK_PERCENT must be between 0 and 100")
        if self.stop_loss_pips <= 0:
            raise ValueError("STOP_LOSS_PIPS must be positive (required for position sizing)")


def load_config() -> Config:
    return Config(
        login=int(_require("MT5_LOGIN")),
        password=_require("MT5_PASSWORD"),
        server=_require("MT5_SERVER"),
        terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        symbol=os.getenv("SYMBOL", "EURUSD"),
        timeframe_name=os.getenv("TIMEFRAME", "M15"),
        fast_ma_period=_int("FAST_MA_PERIOD", 10),
        slow_ma_period=_int("SLOW_MA_PERIOD", 50),
        ma_method=os.getenv("MA_METHOD", "EMA").upper(),
        risk_percent=_float("RISK_PERCENT", 1.0),
        stop_loss_pips=_float("STOP_LOSS_PIPS", 30),
        take_profit_pips=_float("TAKE_PROFIT_PIPS", 60),
        magic_number=_int("MAGIC_NUMBER", 990011),
        deviation_points=_int("DEVIATION_POINTS", 20),
        poll_seconds=_int("POLL_SECONDS", 15),
    )
