"""Configuration for Execution Bridge. Own config, separate from
v3/signal_engine's and v3/alert_manager's -- each bot/component in this
repo owns its own (see CLAUDE.md) -- but reuses the base
MT5_LOGIN/PASSWORD/SERVER/TERMINAL_PATH env vars (same already-running,
already-logged-in MetaTrader5-5 terminal every other bot here connects
to).

Reconciles TWO independent sources, each its own magic number/state
file/comment prefix -- Trend Manager and Reversal Manager can each hold
a position on the same symbol simultaneously (confirmed 2026-08-17,
"both can open same direction or opposite direction trades"), so they
need fully separate tracking, not just separate decisions. Lot sizes
and Stoploss Manager's trailing thresholds are shared across both
sources for the same symbol (not asked to differ per source -- assumed
the same trailing rule applies regardless of which system opened the
position, since the whole point of reusing Stoploss Manager wholesale
for Reversal Manager was "rest sl manager takes the job").
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
class SourceConfig:
    name: str            # "trend" or "reversal" -- for log lines only
    magic_number: int
    comment_prefix: str  # "TM" or "RM"
    decision_state_file: str  # the Manager's own state file (read-only)
    order_state_file: str     # Execution Bridge's own tracking for this source
    sl_state_file: str        # Stoploss Manager's own trailing state for this source
    # Where a REAL manual cancel/close gets relayed back to this
    # source's own Manager (see manual_events.py) -- None for sources
    # that don't have that feedback loop wired up yet (Reversal
    # Manager doesn't consume this file at all currently, only Trend
    # Manager's trade_tracker.py does).
    manual_events_file: Optional[str] = None


@dataclass(frozen=True)
class Config:
    mt5_terminal_path: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    deviation_points: int
    poll_seconds: float
    enable_trading: bool
    sources: list  # list[SourceConfig]
    symbols: list  # list[SymbolConfig]


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    return Config(
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        deviation_points=int(os.getenv("EXECUTION_BRIDGE_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("EXECUTION_BRIDGE_POLL_SECONDS", "2.0")),
        # Same convention as every other bot in this repo (see
        # CLAUDE.md): defaults false, nothing sends/cancels/modifies a
        # real order until this is explicitly set true in .env. Shared
        # by both sources -- one flag gates all real MT5 actions here.
        enable_trading=os.getenv("EXECUTION_BRIDGE_ENABLE_TRADING", "false").strip().lower() == "true",
        sources=[
            SourceConfig(
                name="trend",
                magic_number=int(os.getenv("TREND_MANAGER_MAGIC_NUMBER", "26081701")),
                comment_prefix="TM",
                decision_state_file=os.getenv("SIGNAL_ENGINE_TRADE_STATE_FILE", "trend_manager_trade_state.json"),
                order_state_file=os.getenv("EXECUTION_BRIDGE_ORDER_STATE_FILE", "execution_bridge_orders.json"),
                sl_state_file=os.getenv("EXECUTION_BRIDGE_SL_STATE_FILE", "execution_bridge_sl_state.json"),
                manual_events_file=os.getenv("EXECUTION_BRIDGE_MANUAL_EVENTS_FILE", "execution_bridge_manual_events.json"),
            ),
            SourceConfig(
                name="reversal",
                magic_number=int(os.getenv("REVERSAL_MANAGER_MAGIC_NUMBER", "26081801")),
                comment_prefix="RM",
                decision_state_file=os.getenv("REVERSAL_MANAGER_STATE_FILE", "reversal_manager_state.json"),
                order_state_file=os.getenv("EXECUTION_BRIDGE_REVERSAL_ORDER_STATE_FILE",
                                            "execution_bridge_orders_reversal.json"),
                sl_state_file=os.getenv("EXECUTION_BRIDGE_REVERSAL_SL_STATE_FILE",
                                         "execution_bridge_sl_state_reversal.json"),
            ),
        ],
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
