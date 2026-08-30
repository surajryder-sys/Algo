"""Configuration for V4's profit-alerts bot, loaded from environment
variables (.env). Reuses the base MT5_LOGIN/PASSWORD/SERVER/
TERMINAL_PATH -- same already-running, already-logged-in terminal
every V4 (and formerly V3) MT5-touching component connects to -- and
the SAME Telegram bot/chat v3/profit_alerts/ used
(PROFIT_ALERTS_TELEGRAM_BOT_TOKEN/CHAT_ID), per the user's own explicit
call to keep using SecretTrader_Critical_Bot.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str  # MT5 symbol name (plain, no broker suffix)
    # Ascending list of profit milestones, in raw price-distance points --
    # unchanged from v3/profit_alerts/'s own values, user's explicit
    # confirmation 2026-08-28 ("same as before"): XAUUSD 12/25, BTCUSD
    # 500/1000, ETHUSD 20/40. NOT cumulative -- each fires its own
    # separate alert once.
    milestones: List[float]
    # Which V4 component's magic number owns this symbol -- V4 XAUUSD
    # Trend Manager (its own dedicated magic) vs V4 crypto Trend Manager
    # (one shared magic for BOTH BTCUSD and ETHUSD).
    magic_number: int


@dataclass(frozen=True)
class Config:
    mt5_terminal_path: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    telegram_bot_token: str
    telegram_chat_id: str
    poll_seconds: float
    symbols: List[SymbolConfig]
    state_file: str


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    xauusd_magic = int(os.getenv("V4_MAGIC_NUMBER", "28082801"))
    crypto_magic = int(os.getenv("CRYPTO_TM_MAGIC_NUMBER", "29082901"))
    return Config(
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        telegram_bot_token=os.getenv("PROFIT_ALERTS_TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("PROFIT_ALERTS_TELEGRAM_CHAT_ID", ""),
        poll_seconds=float(os.getenv("V4_PROFIT_ALERTS_POLL_SECONDS", "5.0")),
        symbols=[
            SymbolConfig("XAUUSD", [12.0, 25.0], xauusd_magic),
            SymbolConfig("BTCUSD", [500.0, 1000.0], crypto_magic),
            SymbolConfig("ETHUSD", [20.0, 40.0], crypto_magic),
        ],
        state_file=os.getenv("V4_PROFIT_ALERTS_STATE_FILE", "v4_profit_alerts_state.json"),
    )
