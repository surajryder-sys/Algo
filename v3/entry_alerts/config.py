"""Configuration for the entry-alerts bot, loaded from environment
variables (.env). Reuses the base MT5_LOGIN/PASSWORD/SERVER/
TERMINAL_PATH -- same already-running, already-logged-in terminal
every other v3 MT5-touching component connects to -- and its OWN
separate Telegram bot/chat (ENTRY_ALERTS_TELEGRAM_BOT_TOKEN/CHAT_ID,
added 2026-08-25), deliberately not Alert Manager's or profit_alerts's
own bots.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    mt5_terminal_path: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    telegram_bot_token: str
    telegram_chat_id: str
    poll_seconds: float
    symbols: List[str]
    # Trend Manager's own magic number only -- "one more bot for trade
    # manager," user's own scope 2026-08-25. Reuses Trend Manager's own
    # env var (not a separate copy) so a future change can't silently
    # drift out of sync with this bot.
    magic_number: int
    state_file: str


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    return Config(
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        telegram_bot_token=os.getenv("ENTRY_ALERTS_TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("ENTRY_ALERTS_TELEGRAM_CHAT_ID", ""),
        poll_seconds=float(os.getenv("ENTRY_ALERTS_POLL_SECONDS", "5.0")),
        symbols=["XAUUSD", "BTCUSD", "ETHUSD"],
        magic_number=int(os.getenv("TREND_MANAGER_MAGIC_NUMBER", "26081701")),
        state_file=os.getenv("ENTRY_ALERTS_STATE_FILE", "entry_alerts_state.json"),
    )
