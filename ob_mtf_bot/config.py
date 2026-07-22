"""Loads OB MTF bot configuration from environment variables (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # Optional: if omitted, connect() attaches to an already-running, already
    # logged-in terminal instead of authenticating with these.
    login: int | None
    password: str | None
    server: str | None
    terminal_path: str | None

    symbol: str

    lots: float
    sl_buffer: float
    min_sl_distance: float

    magic_number: int
    deviation_points: int

    enable_trading: bool
    enable_trailing: bool

    poll_seconds: int

    def __post_init__(self) -> None:
        if self.lots <= 0:
            raise ValueError("OB_LOTS must be positive")
        if self.min_sl_distance <= 0:
            raise ValueError("OB_MIN_SL_DISTANCE must be positive")
        if self.poll_seconds <= 0:
            raise ValueError("OB_POLL_SECONDS must be positive")


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN")
    return Config(
        login=int(login_raw) if login_raw else None,
        password=os.getenv("MT5_PASSWORD") or None,
        server=os.getenv("MT5_SERVER") or None,
        terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        symbol=os.getenv("OB_SYMBOL", "XAUUSD"),
        lots=_float("OB_LOTS", 0.01),
        sl_buffer=_float("OB_SL_BUFFER", 0.50),
        min_sl_distance=_float("OB_MIN_SL_DISTANCE", 7.00),
        magic_number=_int("OB_MAGIC_NUMBER", 26071502),
        deviation_points=_int("OB_DEVIATION_POINTS", 30),
        enable_trading=_bool("OB_ENABLE_TRADING", True),
        enable_trailing=_bool("OB_ENABLE_TRAILING", True),
        poll_seconds=_int("OB_POLL_SECONDS", 5),
    )
