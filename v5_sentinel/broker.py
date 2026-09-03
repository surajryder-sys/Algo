"""Thin wrapper around the MetaTrader5 Python package: connection, price
reads, position queries, and order placement/modification/partial-close.
No strategy logic lives here -- just MT5 plumbing. Market orders only --
V5-Sentinel's execution rule is "candle closes beyond both trail lines",
evaluated on already-closed bars, so there's no pending-order concept
here the way algo_v2's OB-edge entries need one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

from v5_sentinel.config import Config


def connect(cfg: Config) -> None:
    kwargs = {}
    if cfg.mt5_terminal_path:
        kwargs["path"] = cfg.mt5_terminal_path
    if cfg.mt5_login and cfg.mt5_password and cfg.mt5_server:
        kwargs.update(login=cfg.mt5_login, password=cfg.mt5_password, server=cfg.mt5_server)

    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    if not mt5.symbol_select(cfg.symbol, True):
        raise RuntimeError(f"Could not select symbol {cfg.symbol}: {mt5.last_error()}")


def shutdown() -> None:
    mt5.shutdown()


def get_tick_price(symbol: str) -> tuple[float, float]:
    """Returns (bid, ask)."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
    return tick.bid, tick.ask


def get_positions(symbol: str, magic: int):
    positions = mt5.positions_get(symbol=symbol) or ()
    return [p for p in positions if p.magic == magic]


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    retcode: int
    comment: str
    ticket: Optional[int]


def _result_from(result) -> OrderResult:
    if result is None:
        return OrderResult(False, -1, f"order_send returned None: {mt5.last_error()}", None)
    ok = result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
    ticket = getattr(result, "order", None) or getattr(result, "deal", None)
    return OrderResult(ok, result.retcode, result.comment, ticket)


def send_market_order(symbol: str, direction: int, lots: float, sl: float, magic: int,
                      deviation: int, comment: str) -> OrderResult:
    """No tp= here on purpose -- Trade Manager books profit as direct
    partial closes, the bot never places a broker-side TP itself."""
    bid, ask = get_tick_price(symbol)
    price = ask if direction == 1 else bid
    order_type = mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lots,
        "type": order_type,
        "price": price,
        "sl": sl,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return _result_from(mt5.order_send(request))


def modify_position_sl(symbol: str, ticket: int, new_sl: float, tp: float = 0.0) -> OrderResult:
    """tp=0.0 default is a no-tp modify, not "clear the tp" -- callers
    that need to preserve an existing manual TP must pass it through
    explicitly (see trade_manager.py's TP-pause check for why this
    matters: a manual TP must survive an SL-only modify untouched)."""
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": new_sl,
        "tp": tp,
    }
    return _result_from(mt5.order_send(request))


def close_position(symbol: str, position, deviation: int, volume: Optional[float] = None,
                   comment: str = "V5S close") -> OrderResult:
    """volume=None closes the position's FULL current volume; a smaller
    volume performs a partial close (broker volume-step permitting) --
    used by trade_manager.py's 70%/15% booking."""
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = get_tick_price(symbol)
    price = bid if direction == 1 else ask
    close_type = mt5.ORDER_TYPE_SELL if direction == 1 else mt5.ORDER_TYPE_BUY
    close_volume = position.volume if volume is None else volume

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": close_volume,
        "type": close_type,
        "position": position.ticket,
        "price": price,
        "deviation": deviation,
        "magic": position.magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return _result_from(mt5.order_send(request))


def has_manual_tp(position) -> bool:
    """True if the position currently carries a broker-side TP -- the
    bot itself never sets one (see send_market_order), so any TP present
    was set manually and should pause Trade Manager's automatic
    %-exits (see trade_manager.py)."""
    return bool(position.tp) and position.tp != 0.0
