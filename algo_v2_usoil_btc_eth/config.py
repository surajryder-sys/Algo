"""Configuration for the merged USOIL+BTCUSD+ETHUSD V2 bot, loaded from
environment variables (.env). One process, ONE shared MT5 connection
(deliberately -- avoids running three separate IPC connections to the same
terminal, which is what running algo_v2_usoil/algo_v2_btc/algo_v2_eth as
three independent processes would have meant now that they'd all point at
the same terminal anyway). Each symbol still gets its own magic number,
lots, state/block files, and entry/SL constants (see entries.py) -- only
the connection and poll loop are shared.

SMC_V2_USOIL_* env vars are kept exactly as they were under the old
algo_v2_usoil package (backward compatible with the existing .env) for the
already-configured USOIL symbol. BTCUSD/ETHUSD are new: SMC_V2_BTC_* /
SMC_V2_ETH_*. Connection-level settings (shared across all three) are
SMC_V2_MULTI_*.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    lots: float
    magic_number: int
    deviation_points: int
    state_file: str
    blocked_state_file: str
    atr_timeframe_minutes: int  # always 15 -- M15 is the zone anchor for every symbol here


@dataclass(frozen=True)
class Config:
    poll_seconds: float
    enable_trading: bool
    symbols: list  # list[SymbolConfig], in polling order

    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None


def _symbol_config(prefix: str, symbol_default: str, magic_default: str,
                   lots_default: str) -> SymbolConfig:
    return SymbolConfig(
        symbol=os.getenv(f"{prefix}_SYMBOL", symbol_default),
        lots=float(os.getenv(f"{prefix}_LOTS", lots_default)),
        magic_number=int(os.getenv(f"{prefix}_MAGIC_NUMBER", magic_default)),
        deviation_points=int(os.getenv(f"{prefix}_DEVIATION_POINTS", "30")),
        state_file=os.getenv(f"{prefix}_STATE_FILE", f"smc_v2_{symbol_default.lower()}_bot_state.json"),
        blocked_state_file=os.getenv(f"{prefix}_BLOCKED_STATE_FILE", f"smc_v2_{symbol_default.lower()}_bot_blocks.json"),
        atr_timeframe_minutes=int(os.getenv(f"{prefix}_ATR_TIMEFRAME_MINUTES", "15")),
    )


def load_config() -> Config:
    login_raw = os.getenv("SMC_V2_MULTI_MT5_LOGIN", "").strip()

    symbols = [
        _symbol_config("SMC_V2_USOIL", "USOIL", "26080501", "0.02"),
        _symbol_config("SMC_V2_BTC", "BTCUSD", "26080801", "0.01"),
        _symbol_config("SMC_V2_ETH", "ETHUSD", "26080802", "0.1"),
    ]

    return Config(
        poll_seconds=float(os.getenv("SMC_V2_MULTI_POLL_SECONDS", "1")),
        enable_trading=_env_bool("SMC_V2_MULTI_ENABLE_TRADING", False),
        symbols=symbols,
        # Own dedicated terminal install -- see module docstring. Falls
        # back to the USOIL-only var name if the new MULTI one isn't set,
        # so an existing .env keeps working without edits.
        mt5_terminal_path=(os.getenv("SMC_V2_MULTI_MT5_TERMINAL_PATH")
                            or os.getenv("SMC_V2_USOIL_MT5_TERMINAL_PATH")
                            or None),
        mt5_login=int(login_raw) if login_raw else None,
        mt5_password=os.getenv("SMC_V2_MULTI_MT5_PASSWORD") or None,
        mt5_server=os.getenv("SMC_V2_MULTI_MT5_SERVER") or None,
    )
