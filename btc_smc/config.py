"""Configuration for the SMC BTCUSD bot, loaded from environment variables
(.env). Fully independent from the XAUUSD (algo/) and ETHUSD (eth_smc/)
bots: separate env var prefix (BTC_SMC_*) and its own MT5 terminal
connection, since this bot runs against a third, separate MT5 terminal
install so it never touches the other two.
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
    alert_state_file: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> Config:
    login_raw = os.getenv("BTC_SMC_MT5_LOGIN", "").strip()

    return Config(
        symbol=os.getenv("BTC_SMC_SYMBOL", "BTCUSD"),
        lots=float(os.getenv("BTC_SMC_LOTS", "0.05")),
        magic_number=int(os.getenv("BTC_SMC_MAGIC_NUMBER", "26072801")),
        deviation_points=int(os.getenv("BTC_SMC_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("BTC_SMC_POLL_SECONDS", "1")),
        enable_trading=_env_bool("BTC_SMC_ENABLE_TRADING", False),
        state_file=os.getenv("BTC_SMC_STATE_FILE", "btc_smc_bot_state.json"),
        blocked_state_file=os.getenv("BTC_SMC_BLOCKED_STATE_FILE", "btc_smc_bot_blocks.json"),
        alert_state_file=os.getenv("BTC_SMC_ALERT_STATE_FILE", "btc_smc_bot_alerts.json"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        mt5_terminal_path=os.getenv(
            "BTC_SMC_MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader5-4\terminal64.exe"
        ) or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("BTC_SMC_MT5_PASSWORD") or None,
        mt5_server=os.getenv("BTC_SMC_MT5_SERVER") or None,
    )
