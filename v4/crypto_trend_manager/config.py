"""Configuration for V4's crypto Trend Manager (BTCUSD + ETHUSD, one shared
process/MT5 connection -- see main.py's own docstring for why one process
handles both instead of two independent ones, unlike XAUUSD's trend_manager).

Own CRYPTO_TM_ prefix, own state file, own magic number -- independent of
V4's XAUUSD trend_manager and of the old (stopped) v3 crypto Trend
Manager/Execution Bridge, per this repo's usual per-bot isolation (see
CLAUDE.md). No MT5_LOGIN/PASSWORD/SERVER/TERMINAL_PATH override by
default -- same as v4/trend_manager/config.py, attaches to whatever MT5
terminal is already running (confirmed live: that's MetaTrader5-5,
already shared by V4/XAUUSD and algo_v2_usoil_btc_eth -- the user
explicitly does not want another terminal instance opened, "i cannot
open too many charts on MT5, that slows down the application").
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

SYMBOLS = ("BTCUSD", "ETHUSD")
PRIMARY_SYMBOL = "BTCUSD"


@dataclass(frozen=True)
class Config:
    poll_seconds: float
    state_file: str
    exit_manager_state_file: str
    mt5_terminal_path: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    magic_number: int
    lot_sizes: dict[str, float]
    enable_trading: bool
    fatal_retry_cooldown_seconds: float


def load_config() -> Config:
    login = os.getenv("CRYPTO_TM_MT5_LOGIN")
    return Config(
        poll_seconds=float(os.getenv("CRYPTO_TM_POLL_SECONDS", "2")),
        state_file=os.getenv("CRYPTO_TM_STATE_FILE", "v4_crypto_trend_manager_state.json"),
        exit_manager_state_file=os.getenv("CRYPTO_TM_EXIT_MANAGER_STATE_FILE", "v4_crypto_exit_manager_state.json"),
        mt5_terminal_path=os.getenv("CRYPTO_TM_MT5_TERMINAL_PATH") or None,
        mt5_login=int(login) if login else None,
        mt5_password=os.getenv("CRYPTO_TM_MT5_PASSWORD") or None,
        mt5_server=os.getenv("CRYPTO_TM_MT5_SERVER") or None,
        # Own dedicated magic number -- doesn't collide with V4/XAUUSD
        # (28082801) or the old, now-stopped v3 crypto engine (26081701).
        magic_number=int(os.getenv("CRYPTO_TM_MAGIC_NUMBER", "29082901")),
        # Same lot sizes previously proven for these two symbols under the
        # old v3 Execution Bridge (EXECUTION_BRIDGE_BTCUSD_LOTS/
        # EXECUTION_BRIDGE_ETHUSD_LOTS) -- reused as sensible defaults,
        # independently overridable here.
        lot_sizes={
            "BTCUSD": float(os.getenv("CRYPTO_TM_BTCUSD_LOTS", "0.05")),
            "ETHUSD": float(os.getenv("CRYPTO_TM_ETHUSD_LOTS", "1.0")),
        },
        enable_trading=os.getenv("CRYPTO_TM_ENABLE_TRADING", "false").strip().lower() == "true",
        # 2026-09-02 fix -- see engine.py's FATAL_RETCODES/fatal_failure_active:
        # cooldown before the SAME confirmation is retried again after a
        # non-retryable rejection (no money, market closed). A genuinely
        # new signal is never held back by it.
        fatal_retry_cooldown_seconds=float(os.getenv("CRYPTO_TM_FATAL_RETRY_COOLDOWN_SECONDS", "300")),
    )
