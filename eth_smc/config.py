"""Configuration for the SMC ETHUSD bot, loaded from environment variables
(.env). Fully independent from the XAUUSD bot's algo/config.py: separate
env var prefix (ETH_SMC_*) and its own MT5 terminal connection, since this
bot runs against a second, separate MT5 terminal install so it never
touches the XAUUSD terminal that's already running live.
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
    login_raw = os.getenv("ETH_SMC_MT5_LOGIN", "").strip()

    return Config(
        symbol=os.getenv("ETH_SMC_SYMBOL", "ETHUSD"),
        lots=float(os.getenv("ETH_SMC_LOTS", "0.01")),
        magic_number=int(os.getenv("ETH_SMC_MAGIC_NUMBER", "26072701")),
        deviation_points=int(os.getenv("ETH_SMC_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("ETH_SMC_POLL_SECONDS", "1")),
        enable_trading=_env_bool("ETH_SMC_ENABLE_TRADING", False),
        state_file=os.getenv("ETH_SMC_STATE_FILE", "eth_smc_bot_state.json"),
        blocked_state_file=os.getenv("ETH_SMC_BLOCKED_STATE_FILE", "eth_smc_bot_blocks.json"),
        mt5_terminal_path=os.getenv(
            "ETH_SMC_MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader5-2\terminal64.exe"
        ) or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("ETH_SMC_MT5_PASSWORD") or None,
        mt5_server=os.getenv("ETH_SMC_MT5_SERVER") or None,
    )
