"""Configuration for the SMC XAUUSD bot, loaded from environment variables
(.env). Mirrors the connection conventions already used by the other bots
in this repo: leave MT5 login blank to attach to whatever account is
already logged into the terminal (recommended, safer) instead of forcing
a fresh login.
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
    state_file: str
    blocked_state_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()

    return Config(
        symbol=os.getenv("SMC_SYMBOL", "XAUUSD"),
        lots=float(os.getenv("SMC_LOTS", "0.01")),
        magic_number=int(os.getenv("SMC_MAGIC_NUMBER", "26072501")),
        deviation_points=int(os.getenv("SMC_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("SMC_POLL_SECONDS", "1")),
        enable_trading=_env_bool("SMC_ENABLE_TRADING", False),
        state_file=os.getenv("SMC_STATE_FILE", "smc_bot_state.json"),
        blocked_state_file=os.getenv("SMC_BLOCKED_STATE_FILE", "smc_bot_blocks.json"),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
    )
