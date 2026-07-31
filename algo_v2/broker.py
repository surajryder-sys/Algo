"""Thin wrapper around the MetaTrader5 Python package: connection, price
reads, position/pending-order queries, and order placement/modification.
No strategy logic lives here -- just MT5 plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

from algo_v2.config import Config


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


def get_positions_by_direction(symbol: str, magic: int, direction: int):
    want_type = mt5.POSITION_TYPE_BUY if direction == 1 else mt5.POSITION_TYPE_SELL
    return [p for p in get_positions(symbol, magic) if p.type == want_type]


def get_pending_orders(symbol: str, magic: int):
    orders = mt5.orders_get(symbol=symbol) or ()
    return [o for o in orders if o.magic == magic]


def get_pending_orders_by_direction(symbol: str, magic: int, direction: int):
    want_types = ((mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP) if direction == 1
                  else (mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP))
    return [o for o in get_pending_orders(symbol, magic) if o.type in want_types]


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


def send_pending_order(symbol: str, direction: int, entry: float, lots: float, sl: float,
                       magic: int, deviation: int, comment: str) -> OrderResult:
    bid, ask = get_tick_price(symbol)

    if direction == 1:
        order_type = mt5.ORDER_TYPE_BUY_LIMIT if entry < ask else mt5.ORDER_TYPE_BUY_STOP
    else:
        order_type = mt5.ORDER_TYPE_SELL_LIMIT if entry > bid else mt5.ORDER_TYPE_SELL_STOP

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": lots,
        "type": order_type,
        "price": entry,
        "sl": sl,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return _result_from(mt5.order_send(request))


def cancel_pending_order(ticket: int) -> OrderResult:
    request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
    return _result_from(mt5.order_send(request))


def modify_position_sl(symbol: str, ticket: int, new_sl: float, tp: float = 0.0) -> OrderResult:
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": new_sl,
        "tp": tp,
    }
    return _result_from(mt5.order_send(request))


def close_position(symbol: str, position, deviation: int) -> OrderResult:
    direction = 1 if position.type == mt5.POSITION_TYPE_BUY else -1
    bid, ask = get_tick_price(symbol)
    price = bid if direction == 1 else ask
    close_type = mt5.ORDER_TYPE_SELL if direction == 1 else mt5.ORDER_TYPE_BUY

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": position.volume,
        "type": close_type,
        "position": position.ticket,
        "price": price,
        "deviation": deviation,
        "magic": position.magic,
        "comment": "V2 bias-flip close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return _result_from(mt5.order_send(request))
