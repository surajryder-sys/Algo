"""Configuration for the profit-alerts bot, loaded from environment
variables (.env). Reuses the base MT5_LOGIN/PASSWORD/SERVER/
TERMINAL_PATH -- same already-running, already-logged-in terminal
every other v3 MT5-touching component connects to -- and its OWN
separate Telegram bot/chat (PROFIT_ALERTS_TELEGRAM_BOT_TOKEN/CHAT_ID,
added 2026-08-25), deliberately not Alert Manager's retest-alert bot.
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
    # Ascending list of profit milestones, in raw price-distance points
    # (same unit as every other symbol-scaled distance in this repo --
    # e.g. XAUUSD's entries.py sl_buffer=1.0 means $1, not a broker
    # pip/point) -- NOT cumulative, each fires its own separate alert
    # once, per the user's explicit "Both separate alerts" (2026-08-25):
    # XAUUSD 12/25, BTCUSD 500/1000, ETHUSD 20/40.
    milestones: List[float]


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
    # Only positions carrying one of these magic numbers count -- "only
    # this system's own trades" (user's explicit scope 2026-08-25), not
    # every position on the account. Trend Manager's + Reversal
    # Manager's, reusing their OWN env vars (not a separate copy) so a
    # future change to either magic number can't silently drift out of
    # sync with this bot.
    magic_numbers: List[int]
    state_file: str


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    return Config(
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        telegram_bot_token=os.getenv("PROFIT_ALERTS_TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("PROFIT_ALERTS_TELEGRAM_CHAT_ID", ""),
        poll_seconds=float(os.getenv("PROFIT_ALERTS_POLL_SECONDS", "5.0")),
        symbols=[
            SymbolConfig("XAUUSD", [12.0, 25.0]),
            SymbolConfig("BTCUSD", [500.0, 1000.0]),
            SymbolConfig("ETHUSD", [20.0, 40.0]),
        ],
        magic_numbers=[
            int(os.getenv("TREND_MANAGER_MAGIC_NUMBER", "26081701")),
            int(os.getenv("REVERSAL_MANAGER_MAGIC_NUMBER", "26081801")),
        ],
        state_file=os.getenv("PROFIT_ALERTS_STATE_FILE", "profit_alerts_state.json"),
    )
