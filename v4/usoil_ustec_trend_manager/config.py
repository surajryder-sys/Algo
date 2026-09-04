"""Configuration for V4's USOIL/USTEC Trend Manager -- one shared process/
MT5 connection for both symbols, same reasoning as crypto_trend_manager's
own config.py (one process is simpler than two when they read from the
SAME shared tv_scraper window anyway). Unlike BTCUSD/ETHUSD, there is NO
primary/secondary relationship between USOIL and USTEC -- both trade
purely on their own independent signals (explicit user choice, 2026-08-30).

Own USOIL_USTEC_TM_ prefix, own state file, own magic number -- independent
of every other bot here (see CLAUDE.md). No MT5_LOGIN/PASSWORD/SERVER/
TERMINAL_PATH override by default -- same as every other V4 bot, attaches
to whatever MT5 terminal is already running.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

SYMBOLS = ("USOIL", "USTEC")


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
    login = os.getenv("USOIL_USTEC_TM_MT5_LOGIN")
    return Config(
        poll_seconds=float(os.getenv("USOIL_USTEC_TM_POLL_SECONDS", "2")),
        state_file=os.getenv("USOIL_USTEC_TM_STATE_FILE", "v4_usoil_ustec_trend_manager_state.json"),
        exit_manager_state_file=os.getenv("USOIL_USTEC_TM_EXIT_MANAGER_STATE_FILE",
                                           "v4_usoil_ustec_exit_manager_state.json"),
        mt5_terminal_path=os.getenv("USOIL_USTEC_TM_MT5_TERMINAL_PATH") or None,
        mt5_login=int(login) if login else None,
        mt5_password=os.getenv("USOIL_USTEC_TM_MT5_PASSWORD") or None,
        mt5_server=os.getenv("USOIL_USTEC_TM_MT5_SERVER") or None,
        # Own dedicated magic number -- doesn't collide with V4/XAUUSD
        # (28082801), crypto_trend_manager (29082901), or the old,
        # now-stopped v3 signal_engine (26081701).
        magic_number=int(os.getenv("USOIL_USTEC_TM_MAGIC_NUMBER", "31083101")),
        # User's explicit numbers, 2026-08-31 (raised from the old v3
        # signal_engine's 0.04/0.2 for clean partial-booking amounts --
        # same reasoning that raise had the first time, per git history).
        lot_sizes={
            "USOIL": float(os.getenv("USOIL_USTEC_TM_USOIL_LOTS", "0.05")),
            "USTEC": float(os.getenv("USOIL_USTEC_TM_USTEC_LOTS", "0.25")),
        },
        enable_trading=os.getenv("USOIL_USTEC_TM_ENABLE_TRADING", "false").strip().lower() == "true",
        # 2026-09-02 fix: a broker rejection that will NEVER succeed on an
        # identical immediate retry (e.g. no money) used to get retried
        # every poll_seconds forever -- confirmed live, one stuck USOIL
        # signal generated ~20,000 failed order-send calls in a single day.
        # This is the cooldown before the SAME confirmation is attempted
        # again after a non-retryable rejection; a genuinely new signal is
        # never held back by it. See engine.py's FATAL_RETCODES/
        # fatal_failure_active.
        fatal_retry_cooldown_seconds=float(os.getenv("USOIL_USTEC_TM_FATAL_RETRY_COOLDOWN_SECONDS", "300")),
    )
