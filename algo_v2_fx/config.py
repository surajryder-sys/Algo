"""Configuration for the FX cross-pairs bot (H1 order-block pullback entries
only), loaded from environment variables (.env). One process, many symbols --
unlike algo_v2/algo_v2_usoil (one full bot per instrument), this bot loops
over FX_SYMBOLS each poll since the logic itself (single timeframe, single
entry mechanism) doesn't need per-symbol tuning the way the multi-timeframe
XAUUSD/USOIL bots do. Shares the main MT5_LOGIN/PASSWORD/SERVER/TERMINAL_PATH
vars -- attach to whichever terminal already has these pairs' charts open,
same as algo_v2 does for XAUUSD.
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
    symbols: tuple[str, ...]
    lots: float
    magic_number: int
    deviation_points: int
    poll_seconds: float
    enable_trading: bool
    state_file: str

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def load_config() -> Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    symbols_raw = os.getenv(
        "FX_SYMBOLS",
        "GBPJPY,EURAUD,AUDCHF,GBPAUD,AUDJPY,CHFJPY,CADCHF,AUDNZD,EURJPY",
    )
    symbols = tuple(s.strip() for s in symbols_raw.split(",") if s.strip())

    return Config(
        symbols=symbols,
        lots=float(os.getenv("FX_LOTS", "0.20")),
        magic_number=int(os.getenv("FX_MAGIC_NUMBER", "26080601")),
        deviation_points=int(os.getenv("FX_DEVIATION_POINTS", "30")),
        poll_seconds=float(os.getenv("FX_POLL_SECONDS", "1")),
        enable_trading=_env_bool("FX_ENABLE_TRADING", False),
        state_file=os.getenv("FX_STATE_FILE", "fx_bot_state.json"),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
    )
