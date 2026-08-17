"""Thin MT5 connection + live tick price wrapper -- same connect pattern
as algo_v2/broker.py (path-only initialize against the already-running,
already-logged-in terminal; MT5_LOGIN/PASSWORD/SERVER are blank in this
setup, so never actually supplied), kept as its own small copy here
rather than importing algo_v2.broker directly -- bots in this repo don't
import from each other's folders (see CLAUDE.md), and Alert Manager is a
peer to algo_v2, not a dependent of it.
"""
from __future__ import annotations

import MetaTrader5 as mt5

from v3.alert_manager.config import Config


def connect(cfg: Config) -> None:
    kwargs = {}
    if cfg.mt5_terminal_path:
        kwargs["path"] = cfg.mt5_terminal_path
    if cfg.mt5_login and cfg.mt5_password and cfg.mt5_server:
        kwargs.update(login=cfg.mt5_login, password=cfg.mt5_password, server=cfg.mt5_server)

    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")


def shutdown() -> None:
    mt5.shutdown()


def get_mid_price(symbol: str) -> float:
    """(bid+ask)/2 as the single reference price for "has this zone been
    entered" -- a plain midpoint rather than picking bid or ask alone,
    since neither side is more correct for a symmetric zone-touch check
    (unlike an actual order fill, which does care which side of the
    spread it crosses)."""
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
    return (tick.bid + tick.ask) / 2
