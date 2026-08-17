"""Thin MT5 connection + live tick price wrapper -- same connect pattern
as algo_v2/broker.py (path-only initialize against the already-running,
already-logged-in terminal; MT5_LOGIN/PASSWORD/SERVER are blank in this
setup, so never actually supplied), kept as its own small copy here
rather than importing algo_v2.broker directly -- bots in this repo don't
import from each other's folders (see CLAUDE.md), and Alert Manager is a
peer to algo_v2, not a dependent of it.

Includes auto-reconnect -- confirmed live (2026-08-17): the MT5<->Python
IPC connection can drop while the terminal process itself keeps running
fine (multiple processes -- 3x tv_scraper, 2x Alert Manager, plus
algo_v2's own bots -- all connect to the same terminal), and without
this, a single dropped connection left Alert Manager silently checking
nothing for ~2 hours before anyone noticed. Every failure is now logged
loudly (not silent) and one reconnect is attempted, rate-limited so a
sustained outage doesn't hammer mt5.initialize() every single poll.
"""
from __future__ import annotations

import time
from typing import Optional

import MetaTrader5 as mt5

from v3.alert_manager.config import Config

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


def _get_mid_price_once(symbol: str) -> float:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
    return (tick.bid + tick.ask) / 2


def get_mid_price(symbol: str) -> float:
    """(bid+ask)/2 as the single reference price for "has this zone been
    entered" -- a plain midpoint rather than picking bid or ask alone,
    since neither side is more correct for a symmetric zone-touch check
    (unlike an actual order fill, which does care which side of the
    spread it crosses). On failure, attempts ONE reconnect (rate-limited
    to once per _RECONNECT_COOLDOWN_SECONDS, shared across all symbols,
    so a sustained outage doesn't spam mt5.initialize() every poll) and
    retries once before propagating -- the caller's own per-symbol
    try/except still logs and moves on if this also fails."""
    global _last_reconnect_attempt
    try:
        return _get_mid_price_once(symbol)
    except RuntimeError as exc:
        if _cfg is None:
            raise
        now = time.time()
        if now - _last_reconnect_attempt < _RECONNECT_COOLDOWN_SECONDS:
            raise  # already tried recently -- don't hammer MT5 every single poll
        _last_reconnect_attempt = now
        print(f"[alert_manager] MT5 connection appears broken ({exc}), attempting reconnect...")
        try:
            mt5.shutdown()
            _do_initialize(_cfg)
        except Exception as reconnect_exc:
            print(f"[alert_manager] MT5 reconnect FAILED: {reconnect_exc}")
            raise
        print("[alert_manager] MT5 reconnected successfully")
        return _get_mid_price_once(symbol)
