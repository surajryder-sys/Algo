"""Configuration for the V2 SMC USOIL bot (Order Block + ATR Trail zone
rules), loaded from environment variables (.env). Independent copy of
algo_v2/config.py (the XAUUSD build) -- see algo_v2_usoil/main.py's
docstring for why this is a separate package rather than a config knob on
algo_v2. Runs on its OWN MT5 terminal install (a separate terminal64.exe,
by default the MetaTrader5-3 folder -- same pattern the old eth_smc/
btc_smc bots used), own login/credentials, own state/block files -- fully
independent from algo_v2's XAUUSD terminal.
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
    atr_timeframe_minutes: int

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> Config:
    login_raw = os.getenv("SMC_V2_USOIL_MT5_LOGIN", "").strip()

    return Config(
        symbol=os.getenv("SMC_V2_USOIL_SYMBOL", "USOIL"),
        lots=float(os.getenv("SMC_V2_USOIL_LOTS", "0.01")),
        magic_number=int(os.getenv("SMC_V2_USOIL_MAGIC_NUMBER", "26080501")),
        deviation_points=int(os.getenv("SMC_V2_USOIL_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("SMC_V2_USOIL_POLL_SECONDS", "1")),
        enable_trading=_env_bool("SMC_V2_USOIL_ENABLE_TRADING", False),
        state_file=os.getenv("SMC_V2_USOIL_STATE_FILE", "smc_v2_usoil_bot_state.json"),
        blocked_state_file=os.getenv("SMC_V2_USOIL_BLOCKED_STATE_FILE", "smc_v2_usoil_bot_blocks.json"),
        # 15, not 5: M15 is this bot's zone anchor (see zone.py), so the ATR
        # Trail needs to be computed on the same timeframe -- the indicator
        # must be attached to the M15 USOIL chart for this to have real data.
        atr_timeframe_minutes=int(os.getenv("SMC_V2_USOIL_ATR_TIMEFRAME_MINUTES", "15")),
        # Own dedicated terminal install -- separate from algo_v2's XAUUSD
        # terminal, so this bot never touches the terminal that's already
        # running for gold.
        mt5_terminal_path=os.getenv("SMC_V2_USOIL_MT5_TERMINAL_PATH",
                                     r"C:\Program Files\MetaTrader5-3\terminal64.exe") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("SMC_V2_USOIL_MT5_PASSWORD") or None,
        mt5_server=os.getenv("SMC_V2_USOIL_MT5_SERVER") or None,
    )
