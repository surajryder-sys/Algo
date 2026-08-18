"""Configuration for Execution Bridge. Own config, separate from
v3/signal_engine's and v3/alert_manager's -- each bot/component in this
repo owns its own (see CLAUDE.md) -- but reuses the base
MT5_LOGIN/PASSWORD/SERVER/TERMINAL_PATH env vars (same already-running,
already-logged-in MetaTrader5-5 terminal every other bot here connects
to) and Trend Manager's own reserved magic number (26081701) -- every
order this places IS a Trend Manager order, so it carries Trend
Manager's identity, not a separate one of its own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    lots: float
    # Stoploss Manager's point-based trailing thresholds, user's rule
    # 2026-08-18: breakeven once favor >= breakeven_points, trailing
    # starts at trail_start_points, moving in trail_step_points
    # increments from there. NOTE: these numbers (7/10/2) come from
    # XAUUSD's own price scale (points ~= $1, matching entries.py's
    # 3-12 distance thresholds) -- BTCUSD/ETHUSD move in a completely
    # different range and almost certainly need their own values before
    # trading is ever enabled for them. Defaulted the same as XAUUSD for
    # now only because no crypto-specific numbers were given; flagged,
    # not silently assumed correct.
    breakeven_points: float
    trail_start_points: float
    trail_step_points: float


@dataclass(frozen=True)
class Config:
    mt5_terminal_path: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    magic_number: int
    deviation_points: int
    poll_seconds: float
    enable_trading: bool
    trend_state_file: str
    order_state_file: str
    sl_state_file: str
    symbols: list  # list[SymbolConfig]


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    return Config(
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        magic_number=int(os.getenv("TREND_MANAGER_MAGIC_NUMBER", "26081701")),
        deviation_points=int(os.getenv("EXECUTION_BRIDGE_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("EXECUTION_BRIDGE_POLL_SECONDS", "2.0")),
        # Same convention as every other bot in this repo (see
        # CLAUDE.md): defaults false, nothing sends/cancels/modifies a
        # real order until this is explicitly set true in .env.
        enable_trading=os.getenv("EXECUTION_BRIDGE_ENABLE_TRADING", "false").strip().lower() == "true",
        trend_state_file=os.getenv("SIGNAL_ENGINE_TRADE_STATE_FILE", "trend_manager_trade_state.json"),
        order_state_file=os.getenv("EXECUTION_BRIDGE_ORDER_STATE_FILE", "execution_bridge_orders.json"),
        sl_state_file=os.getenv("EXECUTION_BRIDGE_SL_STATE_FILE", "execution_bridge_sl_state.json"),
        symbols=[
            SymbolConfig(
                "XAUUSD", float(os.getenv("EXECUTION_BRIDGE_XAUUSD_LOTS", "0.04")),
                breakeven_points=float(os.getenv("EXECUTION_BRIDGE_XAUUSD_BREAKEVEN_POINTS", "7")),
                trail_start_points=float(os.getenv("EXECUTION_BRIDGE_XAUUSD_TRAIL_START_POINTS", "10")),
                trail_step_points=float(os.getenv("EXECUTION_BRIDGE_XAUUSD_TRAIL_STEP_POINTS", "2")),
            ),
            SymbolConfig(
                "BTCUSD", float(os.getenv("EXECUTION_BRIDGE_BTCUSD_LOTS", "0.05")),
                breakeven_points=float(os.getenv("EXECUTION_BRIDGE_BTCUSD_BREAKEVEN_POINTS", "7")),
                trail_start_points=float(os.getenv("EXECUTION_BRIDGE_BTCUSD_TRAIL_START_POINTS", "10")),
                trail_step_points=float(os.getenv("EXECUTION_BRIDGE_BTCUSD_TRAIL_STEP_POINTS", "2")),
            ),
            SymbolConfig(
                "ETHUSD", float(os.getenv("EXECUTION_BRIDGE_ETHUSD_LOTS", "1.0")),
                breakeven_points=float(os.getenv("EXECUTION_BRIDGE_ETHUSD_BREAKEVEN_POINTS", "7")),
                trail_start_points=float(os.getenv("EXECUTION_BRIDGE_ETHUSD_TRAIL_START_POINTS", "10")),
                trail_step_points=float(os.getenv("EXECUTION_BRIDGE_ETHUSD_TRAIL_STEP_POINTS", "2")),
            ),
        ],
    )
