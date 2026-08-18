"""Thin wrapper around the MetaTrader5 Python package -- connection,
price reads, position/pending-order queries, order placement/
cancellation/close. v3's own copy of algo_v2/broker.py's shape, NOT an
import -- v3 doesn't share code with algo_v2 in either direction (see
CLAUDE.md). No strategy logic lives here, just MT5 plumbing; every
caller (execution_bridge.py) decides what to place, this module only
knows how.

Order type auto-selection (LIMIT vs STOP) mirrors algo_v2/broker.py's
own defensive logic: a pending BUY at a price below current ask must be
BUY_LIMIT (a LIMIT above ask, or STOP below ask, is invalid and the
broker rejects it). Trend Manager's own retracement entries always sit
between the OB edge and current price, so this resolves to LIMIT in the
normal case, matching the user's explicit confirmation (2026-08-17:
"yes limit orders") -- STOP only ever fires as a fallback for a price
already past the entry by the time the order is sent, which itself is
supposed to have already gone through the market-order fallback instead
(see entries.py's compute_entry) and so should be rare/never in practice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

from v3.execution_bridge.config import Config


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


def get_tick_price(symbol: str) -> tuple[float, float]:
    """Returns (bid, ask)."""
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
    return tick.bid, tick.ask


def get_positions(symbol: str, magic: int):
    positions = mt5.positions_get(symbol=symbol) or ()
    return [p for p in positions if p.magic == magic]


def get_pending_orders(symbol: str, magic: int):
    orders = mt5.orders_get(symbol=symbol) or ()
    return [o for o in orders if o.magic == magic]


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


def send_market_order(symbol: str, direction: str, lots: float, sl: Optional[float], magic: int,
                       deviation: int, comment: str) -> OrderResult:
    bid, ask = get_tick_price(symbol)
    price = ask if direction == "bull" else bid
    order_type = mt5.ORDER_TYPE_BUY if direction == "bull" else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lots,
        "type": order_type,
        "price": price,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    if sl is not None:
        request["sl"] = sl
    return _result_from(mt5.order_send(request))


def send_pending_order(symbol: str, direction: str, entry: float, lots: float, sl: Optional[float],
                        magic: int, deviation: int, comment: str) -> OrderResult:
    bid, ask = get_tick_price(symbol)

    if direction == "bull":
        order_type = mt5.ORDER_TYPE_BUY_LIMIT if entry < ask else mt5.ORDER_TYPE_BUY_STOP
    else:
        order_type = mt5.ORDER_TYPE_SELL_LIMIT if entry > bid else mt5.ORDER_TYPE_SELL_STOP

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": lots,
        "type": order_type,
        "price": entry,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    if sl is not None:
        request["sl"] = sl
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


def close_position(symbol: str, position, deviation: int, comment: str) -> OrderResult:
    direction = "bull" if position.type == mt5.POSITION_TYPE_BUY else "bear"
    bid, ask = get_tick_price(symbol)
    price = bid if direction == "bull" else ask
    close_type = mt5.ORDER_TYPE_SELL if direction == "bull" else mt5.ORDER_TYPE_BUY

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": position.volume,
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
