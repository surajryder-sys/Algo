"""Configuration for V5-Sentinel's critical-alerts bot (SecretTrader_
Critical_Bot) -- renamed and repurposed 2026-09-07 from profit_alerts_*
("stop sending the profit alerts, now it is no longer needed... change
the name to critical alerts"). No longer position/milestone-based at
all -- see critical_alerts_watcher.py for the new support/resistance
touch-alert design. Profit-milestone alerts continue unchanged on the
SAME underlying Telegram bot, just now sent from v5_sentinel.main/
reversal_main directly if ever needed again -- this file/bot is purely
about level-touch alerts now.

Reuses the base MT5_LOGIN/PASSWORD/SERVER/TERMINAL_PATH (same already-
running, already-logged-in terminal every other V5-Sentinel bot connects
to).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    mt5_terminal_path: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    telegram_bot_token: str
    owner_chat_id: str
    poll_seconds: float
    symbol: str
    value_epsilon: float   # how much a level's value must move to count as "a new value", not float noise
    state_file: str
    subscribers_file: str


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    return Config(
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        telegram_bot_token=os.getenv("CRITICAL_ALERTS_TELEGRAM_BOT_TOKEN", ""),
        owner_chat_id=os.getenv("CRITICAL_ALERTS_TELEGRAM_CHAT_ID", ""),
        poll_seconds=float(os.getenv("V5S_CRITICAL_ALERTS_POLL_SECONDS", "5.0")),
        symbol=os.getenv("V5S_SYMBOL", "XAUUSD"),
        value_epsilon=float(os.getenv("V5S_CRITICAL_ALERTS_VALUE_EPSILON", "0.05")),
        state_file=os.getenv("V5S_CRITICAL_ALERTS_STATE_FILE", "v5_sentinel_critical_alerts_state.json"),
        subscribers_file=os.getenv("V5S_CRITICAL_ALERTS_SUBSCRIBERS_FILE", "v5_sentinel_critical_alerts_subscribers.json"),
    )
