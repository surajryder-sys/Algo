"""Configuration for V4's Trend Manager, loaded from environment
variables (.env). Own V4_ prefix, own state files -- independent of
algo_v2/v3, per this repo's usual per-bot isolation (see CLAUDE.md)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    symbol: str
    poll_seconds: float
    m1_execution_state_file: str
    exit_manager_state_file: str
    mt5_terminal_path: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    magic_number: int
    lot_size: float
    enable_trading: bool


def load_config() -> Config:
    login = os.getenv("V4_MT5_LOGIN")
    return Config(
        symbol=os.getenv("V4_SYMBOL", "XAUUSD"),
        poll_seconds=float(os.getenv("V4_POLL_SECONDS", "2")),
        m1_execution_state_file=os.getenv("V4_M1_EXECUTION_STATE_FILE", "v4_m1_execution_state.json"),
        exit_manager_state_file=os.getenv("V4_EXIT_MANAGER_STATE_FILE", "v4_exit_manager_state.json"),
        mt5_terminal_path=os.getenv("V4_MT5_TERMINAL_PATH") or None,
        mt5_login=int(login) if login else None,
        mt5_password=os.getenv("V4_MT5_PASSWORD") or None,
        mt5_server=os.getenv("V4_MT5_SERVER") or None,
        # Own dedicated magic number -- doesn't collide with algo_v2 or
        # v3's Trend/Reversal Manager (26081701 / 26081801).
        magic_number=int(os.getenv("V4_MAGIC_NUMBER", "28082801")),
        lot_size=float(os.getenv("V4_LOT_SIZE", "0.05")),
        # Default false, same convention as every other bot in this repo
        # (see CLAUDE.md) -- must be explicitly set true for any real
        # order to be sent. Left unset, every decision is printed but
        # nothing touches the account.
        enable_trading=os.getenv("V4_ENABLE_TRADING", "false").strip().lower() == "true",
    )
