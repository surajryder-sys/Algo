"""Configuration for the standalone V2 SMC USOIL bot (Order Block + ATR
Trail zone rules, M15-anchored), loaded from environment variables (.env).

Preserved snapshot: this is the standalone, single-symbol USOIL bot as it
existed just before being merged into algo_v2_usoil_btc_eth (which now
runs USOIL alongside BTCUSD/ETHUSD in one shared-connection process). Kept
here, fully independent and ready to run on its own, in case USOIL ever
needs to run in isolation again (its own terminal, no BTC/ETH coupling) --
see algo_v2_usoil_btc_eth/main.py's docstring for why the merge happened
and what changed structurally. The trading logic itself (M15 zone anchor,
M5 strict subordinate tier, entry/SL constants, pullback formula) is
identical between the two; this copy just isn't parameterized by symbol.

Runs on its own MT5 terminal install (by default the MetaTrader5-3
folder), own login/credentials, own state/block files -- fully independent
from algo_v2's XAUUSD terminal (and from algo_v2_usoil_btc_eth's, wherever
that ends up running).
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
        mt5_terminal_path=os.getenv("SMC_V2_USOIL_MT5_TERMINAL_PATH",
                                     r"C:\Program Files\MetaTrader5-3\terminal64.exe") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("SMC_V2_USOIL_MT5_PASSWORD") or None,
        mt5_server=os.getenv("SMC_V2_USOIL_MT5_SERVER") or None,
    )
