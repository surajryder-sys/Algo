"""MT5 terminal connection lifecycle for the OB MTF bot."""
from __future__ import annotations

import logging

import MetaTrader5 as mt5

from ob_mtf_bot.config import Config

log = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1, "M2": mt5.TIMEFRAME_M2, "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4, "M5": mt5.TIMEFRAME_M5, "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10, "M12": mt5.TIMEFRAME_M12, "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20, "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1, "H2": mt5.TIMEFRAME_H2, "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4, "H6": mt5.TIMEFRAME_H6, "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1,
}

TIMEFRAME_MINUTES = {
    "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6, "M10": 10, "M12": 12,
    "M15": 15, "M20": 20, "M30": 30, "H1": 60, "H2": 120, "H3": 180, "H4": 240,
    "H6": 360, "H8": 480, "H12": 720, "D1": 1440, "W1": 10080, "MN1": 43200,
}


class MT5ConnectionError(RuntimeError):
    pass


def connect(cfg: Config) -> None:
    # If credentials aren't supplied, attach to an already-running,
    # already-authenticated terminal instead of logging in ourselves.
    have_creds = cfg.login is not None and cfg.password is not None and cfg.server is not None
    init_kwargs = dict(login=cfg.login, password=cfg.password, server=cfg.server) if have_creds else {}
    initialized = (
        mt5.initialize(cfg.terminal_path, **init_kwargs)
        if cfg.terminal_path
        else mt5.initialize(**init_kwargs)
    )
    if not initialized:
        code, desc = mt5.last_error()
        raise MT5ConnectionError(f"MT5 initialize() failed: [{code}] {desc}")

    account = mt5.account_info()
    if account is None:
        code, desc = mt5.last_error()
        disconnect()
        raise MT5ConnectionError(f"Could not read account info: [{code}] {desc}")

    log.info(
        "Connected to MT5: login=%s server=%s balance=%.2f %s trade_allowed=%s",
        account.login, account.server, account.balance, account.currency,
        account.trade_allowed,
    )
    if not account.trade_allowed:
        log.warning("Algo trading is NOT allowed on this account/terminal. "
                    "Enable 'Algo Trading' in MT5 and check the symbol/account permissions.")


def disconnect() -> None:
    mt5.shutdown()


def ensure_symbol(symbol: str) -> None:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise MT5ConnectionError(f"Symbol '{symbol}' not found on this broker")
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            code, desc = mt5.last_error()
            raise MT5ConnectionError(f"Failed to add '{symbol}' to Market Watch: [{code}] {desc}")


def resolve_timeframe(name: str) -> int:
    return TIMEFRAME_MAP[name]


def timeframe_minutes(name: str) -> int:
    return TIMEFRAME_MINUTES[name]
