"""Configuration for V5-Sentinel's profit-alerts bot -- ported from
v4/profit_alerts/ 2026-08-28 ("port it to V5-Sentinel, bond to V5S"),
since V5-Sentinel has now replaced V4's own XAUUSD Trend Manager as the
live XAUUSD trader (v4.trend_manager.main is no longer running; only
v5_sentinel.main is). V4's own profit_alerts keeps its BTCUSD/ETHUSD
coverage (still live via v4.crypto_trend_manager) -- this is XAUUSD
only, bonded to V5S_MAGIC_NUMBER instead of V4's old XAUUSD magic.

Reuses the base MT5_LOGIN/PASSWORD/SERVER/TERMINAL_PATH (same
already-running, already-logged-in terminal V5-Sentinel's own
broker.py connects to) and the SAME Telegram bot as before
(SecretTrader_Critical_Bot, PROFIT_ALERTS_TELEGRAM_BOT_TOKEN/CHAT_ID).
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
    owner_chat_id: str
    poll_seconds: float
    symbol: str
    milestones: List[float]
    magic_number: int
    state_file: str
    subscribers_file: str


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    return Config(
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        telegram_bot_token=os.getenv("PROFIT_ALERTS_TELEGRAM_BOT_TOKEN", ""),
        owner_chat_id=os.getenv("PROFIT_ALERTS_TELEGRAM_CHAT_ID", ""),
        poll_seconds=float(os.getenv("V5S_PROFIT_ALERTS_POLL_SECONDS", "5.0")),
        symbol=os.getenv("V5S_SYMBOL", "XAUUSD"),
        # Unchanged from V3/V4, user's explicit confirmation ("same as
        # before"): 12/25 points, each its own separate alert.
        milestones=[12.0, 25.0],
        magic_number=int(os.getenv("V5S_MAGIC_NUMBER", "26090201")),
        state_file=os.getenv("V5S_PROFIT_ALERTS_STATE_FILE", "v5_sentinel_profit_alerts_state.json"),
        subscribers_file=os.getenv("V5S_PROFIT_ALERTS_SUBSCRIBERS_FILE", "v5_sentinel_profit_alerts_subscribers.json"),
    )
