"""Configuration for the V2 SMC XAUUSD bot (Order Block + ATR Trail zone
rules), loaded from environment variables (.env). Runs alongside the V1
algo/ bot on the same terminal/account -- every state file, magic number,
and block-status filename here is namespaced separately from algo/config.py
so the two bots never collide.
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
    sl_state_file: str
    direction_block_state_file: str
    m1_cooldown_state_file: str
    atr_timeframe_minutes: int

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()

    return Config(
        symbol=os.getenv("SMC_V2_SYMBOL", "XAUUSD"),
        lots=float(os.getenv("SMC_V2_LOTS", "0.01")),
        magic_number=int(os.getenv("SMC_V2_MAGIC_NUMBER", "26073101")),
        deviation_points=int(os.getenv("SMC_V2_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("SMC_V2_POLL_SECONDS", "1")),
        enable_trading=_env_bool("SMC_V2_ENABLE_TRADING", False),
        state_file=os.getenv("SMC_V2_STATE_FILE", "smc_v2_bot_state.json"),
        blocked_state_file=os.getenv("SMC_V2_BLOCKED_STATE_FILE", "smc_v2_bot_blocks.json"),
        sl_state_file=os.getenv("SMC_V2_SL_STATE_FILE", "smc_v2_bot_sl_state.json"),
        direction_block_state_file=os.getenv("SMC_V2_DIRECTION_BLOCK_STATE_FILE",
                                             "smc_v2_bot_direction_blocks.json"),
        m1_cooldown_state_file=os.getenv("SMC_V2_M1_COOLDOWN_STATE_FILE",
                                         "smc_v2_bot_m1_cooldown.json"),
        atr_timeframe_minutes=int(os.getenv("SMC_V2_ATR_TIMEFRAME_MINUTES", "5")),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
    )
