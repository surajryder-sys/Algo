"""Thin, read-only MT5 connection + open-position query for the
profit-alerts bot -- its own tiny copy rather than v5_sentinel/broker.py
(that module's own connect() requires V5-Sentinel's full trading Config,
including lots/SL fields this read-only bot has no use for). Same
auto-reconnect pattern already proven in v3/v4's own profit_alerts.

Never places, modifies, or closes an order -- positions_get() only.
"""
from __future__ import annotations

import time
from typing import Optional

import MetaTrader5 as mt5

from v5_sentinel.profit_alerts_config import Config

_cfg: Optional[Config] = None
_last_reconnect_attempt: float = 0.0
_RECONNECT_COOLDOWN_SECONDS = 5.0


def _do_initialize(cfg: Config) -> None:
    kwargs = {}
    if cfg.mt5_terminal_path:
        kwargs["path"] = cfg.mt5_terminal_path
    if cfg.mt5_login and cfg.mt5_password and cfg.mt5_server:
        kwargs.update(login=cfg.mt5_login, password=cfg.mt5_password, server=cfg.mt5_server)

    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")


def connect(cfg: Config) -> None:
    global _cfg
    _cfg = cfg
    _do_initialize(cfg)


def shutdown() -> None:
    mt5.shutdown()


def _get_positions_once(symbol: str, magic: int) -> list:
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        error = mt5.last_error()
        if error[0] != 1:  # 1 = "no error, just nothing found"
            raise RuntimeError(f"positions_get failed for {symbol}: {error}")
        return []
    return [p for p in positions if p.magic == magic]


def get_positions(symbol: str, magic: int) -> list:
    global _last_reconnect_attempt
    try:
        return _get_positions_once(symbol, magic)
    except RuntimeError as exc:
        if _cfg is None:
            raise
        now = time.time()
        if now - _last_reconnect_attempt < _RECONNECT_COOLDOWN_SECONDS:
            raise
        _last_reconnect_attempt = now
        print(f"[v5_sentinel.profit_alerts] MT5 connection appears broken ({exc}), attempting reconnect...")
        try:
            mt5.shutdown()
            _do_initialize(_cfg)
        except Exception as reconnect_exc:
            print(f"[v5_sentinel.profit_alerts] MT5 reconnect FAILED: {reconnect_exc}")
            raise
        print("[v5_sentinel.profit_alerts] MT5 reconnected successfully")
        return _get_positions_once(symbol, magic)
