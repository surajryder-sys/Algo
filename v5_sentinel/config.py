"""Configuration for V5-Sentinel's Trend Manager, loaded from environment
variables (.env). Fully independent of every other bot in this repo --
own V5S_-prefixed env vars, own magic number, own state files, own MT5
connection. XAUUSD only for now; extending to other symbols is a later
step (buffer/point thresholds below are XAUUSD-tuned).

Safety: V5S_ENABLE_TRADING must be explicitly set to true in .env for any
order to actually be sent/modified/cancelled. Left unset (default false),
every decision is printed but nothing touches the account.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    symbol: str
    lots: float
    magic_number: int
    deviation_points: int
    poll_seconds: float
    enable_trading: bool

    # SL buffer applied beyond the far trail line (initial SL and ongoing
    # trailing SL both use it) -- XAUUSD points.
    sl_buffer: float

    # SL Manager thresholds (points in favor of the trade).
    breakeven_trigger_points: float   # SL -> cost once profit reaches this
    trail_activation_points: float    # far-line trailing starts once profit is at/beyond this (== breakeven_trigger today, see main.py note)

    # Trade Manager partial-booking thresholds (points in favor) and the
    # fraction of the ORIGINAL entry quantity booked at each.
    partial1_trigger_points: float    # 10
    partial1_fraction: float          # 0.70
    partial2_trigger_points: float    # 15
    partial2_fraction: float          # 0.15
    # Remaining fraction (1 - partial1_fraction - partial2_fraction) rides
    # on the trailing SL with no bot-placed TP.

    state_file: str
    sl_state_file: str
    runtime_state_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()

    return Config(
        symbol=os.getenv("V5S_SYMBOL", "XAUUSD"),
        lots=float(os.getenv("V5S_LOTS", "0.01")),
        magic_number=int(os.getenv("V5S_MAGIC_NUMBER", "26090201")),
        deviation_points=int(os.getenv("V5S_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("V5S_POLL_SECONDS", "1")),
        enable_trading=_env_bool("V5S_ENABLE_TRADING", False),
        sl_buffer=float(os.getenv("V5S_SL_BUFFER", "2.0")),
        breakeven_trigger_points=float(os.getenv("V5S_BREAKEVEN_TRIGGER_POINTS", "7")),
        trail_activation_points=float(os.getenv("V5S_TRAIL_ACTIVATION_POINTS", "7")),
        partial1_trigger_points=float(os.getenv("V5S_PARTIAL1_TRIGGER_POINTS", "10")),
        partial1_fraction=float(os.getenv("V5S_PARTIAL1_FRACTION", "0.70")),
        partial2_trigger_points=float(os.getenv("V5S_PARTIAL2_TRIGGER_POINTS", "15")),
        partial2_fraction=float(os.getenv("V5S_PARTIAL2_FRACTION", "0.15")),
        state_file=os.getenv("V5S_STATE_FILE", "v5s_trend_manager_state.json"),
        sl_state_file=os.getenv("V5S_SL_STATE_FILE", "v5s_trend_manager_sl_state.json"),
        runtime_state_file=os.getenv("V5S_RUNTIME_STATE_FILE", "v5s_trend_manager_runtime_state.json"),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
    )
