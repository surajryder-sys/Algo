"""Generic MT5 position primitives shared across strategies: querying and
closing a magic-number-owned position. Strategy-specific order placement
(entry, trailing, reversal handling) lives in reversal_trader.py.
"""
from __future__ import annotations

import logging

import MetaTrader5 as mt5

log = logging.getLogger(__name__)


def managed_position(symbol: str, magic: int):
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None
    for p in positions:
        if p.magic == magic:
            return p
    return None


def has_position(symbol: str, magic: int) -> bool:
    return managed_position(symbol, magic) is not None


def close_position(symbol: str, magic: int, deviation: int) -> bool:
    pos = managed_position(symbol, magic)
    if pos is None:
        return True

    tick = mt5.symbol_info_tick(symbol)
    is_buy = pos.type == mt5.ORDER_TYPE_BUY
    price = tick.bid if is_buy else tick.ask
    order_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": pos.volume,
        "type": order_type,
        "position": pos.ticket,
        "price": price,
        "deviation": deviation,
        "magic": magic,
        "comment": "RTpy|close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error("Position close failed: %s", result)
        return False

    log.info("Closed position ticket=%s", pos.ticket)
    return True
