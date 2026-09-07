"""Thin, read-only MT5 connection + live tick price for the
critical-alerts bot -- renamed 2026-09-07 from profit_alerts_mt5.py and
trimmed: no position queries any more (this bot is no longer position/
profit-based, see critical_alerts_watcher.py), just connect + tick.
Its own tiny copy rather than v5_sentinel/broker.py (that module's own
connect() requires V5-Sentinel's full trading Config, including lots/SL
fields this read-only bot has no use for).

Never places, modifies, or closes an order.
"""
from __future__ import annotations

from typing import Optional

import MetaTrader5 as mt5

from v5_sentinel.critical_alerts_config import Config


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


def get_tick_price(symbol: str) -> Optional[tuple[float, float]]:
    """(bid, ask), or None if there's no live tick right now (market
    closed, symbol not selected, etc.) -- caller should just skip that
    cycle, not guess."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    return tick.bid, tick.ask
