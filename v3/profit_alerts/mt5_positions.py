"""Thin, read-only MT5 connection + open-position query -- same connect
pattern as v3/alert_manager/mt5_price.py (path-only initialize against
the already-running, already-logged-in terminal), including that
module's own auto-reconnect fix (confirmed live 2026-08-17: the
MT5<->Python IPC connection can drop while the terminal itself stays
healthy). Kept as its own small copy here rather than importing
alert_manager's version directly -- each bot in this repo owns its own
MT5 plumbing (see CLAUDE.md) -- but the mechanism is identical.

Never places, modifies, or closes an order -- positions_get() only.
"""
from __future__ import annotations

import time
from typing import List, Optional

import MetaTrader5 as mt5

from v3.profit_alerts.config import Config

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


def _get_positions_once(symbol: str, magic_numbers: List[int]) -> list:
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        error = mt5.last_error()
        # error[0] == 1 is MT5's own "no error, just nothing found" code
        # -- positions_get() returns None for that too, not an empty
        # tuple, so this isn't necessarily a real failure.
        if error[0] != 1:
            raise RuntimeError(f"positions_get failed for {symbol}: {error}")
        return []
    return [p for p in positions if p.magic in magic_numbers]


def get_positions(symbol: str, magic_numbers: List[int]) -> list:
    """Open positions for `symbol` whose magic number is in
    `magic_numbers` -- on failure, attempts ONE reconnect (rate-limited,
    shared across all symbols) and retries once before propagating."""
    global _last_reconnect_attempt
    try:
        return _get_positions_once(symbol, magic_numbers)
    except RuntimeError as exc:
        if _cfg is None:
            raise
        now = time.time()
        if now - _last_reconnect_attempt < _RECONNECT_COOLDOWN_SECONDS:
            raise
        _last_reconnect_attempt = now
        print(f"[profit_alerts] MT5 connection appears broken ({exc}), attempting reconnect...")
        try:
            mt5.shutdown()
            _do_initialize(_cfg)
        except Exception as reconnect_exc:
            print(f"[profit_alerts] MT5 reconnect FAILED: {reconnect_exc}")
            raise
        print("[profit_alerts] MT5 reconnected successfully")
        return _get_positions_once(symbol, magic_numbers)
