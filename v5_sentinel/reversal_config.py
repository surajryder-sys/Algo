"""Configuration for V5-Sentinel's STR Reversal Manager. Shares the same
symbol/lots/SL-buffer/breakeven/partial-booking values as Trend Manager's
own Config (confirmed 2026-09-07: "sl manager exactly works same...trade
manager also works same...partial booking also takes place exactly
same") by reading the SAME V5S_* env vars -- no duplication needed in
.env unless you want RM to actually diverge from TM on one of those.

Only RM-specific settings get their own V5S_RM_* vars: its own magic
number (separate identity from TM, confirmed 2026-09-07), its own
enable_trading toggle (independent go-ahead required, per this project's
per-bot safety convention -- RM going live is a SEPARATE decision from
TM's), and its own state files (so the two managers' persisted ticket/
level state never collide).

Safety: V5S_RM_ENABLE_TRADING must be explicitly set to true in .env for
any order to actually be sent/modified/cancelled. Left unset (default
false), every decision is printed but nothing touches the account --
independently of whatever V5S_ENABLE_TRADING (Trend Manager's own flag)
is set to.
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
class RMConfig:
    symbol: str
    lots: float
    magic_number: int
    deviation_points: int
    poll_seconds: float
    enable_trading: bool

    sl_buffer: float
    breakeven_trigger_points: float
    partial1_trigger_points: float
    partial1_fraction: float
    partial2_trigger_points: float
    partial2_fraction: float

    state_file: str
    sl_state_file: str
    levels_state_file: str
    bridge_flip_state_file: str
    heartbeat_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> RMConfig:
    login_raw = os.getenv("MT5_LOGIN", "").strip()

    return RMConfig(
        symbol=os.getenv("V5S_SYMBOL", "XAUUSD"),
        lots=float(os.getenv("V5S_LOTS", "0.01")),
        magic_number=int(os.getenv("V5S_RM_MAGIC_NUMBER", "26090701")),
        deviation_points=int(os.getenv("V5S_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("V5S_POLL_SECONDS", "1")),
        enable_trading=_env_bool("V5S_RM_ENABLE_TRADING", False),
        sl_buffer=float(os.getenv("V5S_SL_BUFFER", "2.0")),
        breakeven_trigger_points=float(os.getenv("V5S_BREAKEVEN_TRIGGER_POINTS", "7")),
        partial1_trigger_points=float(os.getenv("V5S_PARTIAL1_TRIGGER_POINTS", "10")),
        partial1_fraction=float(os.getenv("V5S_PARTIAL1_FRACTION", "0.70")),
        partial2_trigger_points=float(os.getenv("V5S_PARTIAL2_TRIGGER_POINTS", "15")),
        partial2_fraction=float(os.getenv("V5S_PARTIAL2_FRACTION", "0.15")),
        state_file=os.getenv("V5S_RM_STATE_FILE", "v5s_reversal_manager_state.json"),
        sl_state_file=os.getenv("V5S_RM_SL_STATE_FILE", "v5s_reversal_manager_sl_state.json"),
        levels_state_file=os.getenv("V5S_RM_LEVELS_STATE_FILE", "v5s_reversal_manager_levels_state.json"),
        bridge_flip_state_file=os.getenv("V5S_RM_BRIDGE_FLIP_STATE_FILE", "v5s_reversal_manager_bridge_flip_state.json"),
        heartbeat_file=os.getenv("V5S_RM_HEARTBEAT_FILE", "v5s_reversal_manager_heartbeat.json"),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
    )
