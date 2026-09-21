"""Thin wrapper around the MetaTrader5 Python package: connection, price
reads, position queries, and order placement/modification/partial-close.
No strategy logic lives here -- just MT5 plumbing. Market orders only --
this project's execution rule is "candle closes beyond both trail
lines," evaluated on already-closed bars, so there's no pending-order
concept here the way algo_v2's OB-edge entries need one.

Ported from v5_sentinel/broker.py (2026-09-18) with one deliberate
change: connect() takes explicit MT5 credential arguments instead of a
whole Config object -- V5S's version took the single-symbol Config
dataclass just to reach 4 fields off it, which doesn't fit V6S's
multi-instrument config shape (no single per-process Config, see
config.py). Every other function is unchanged and was already
symbol-parametrized.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5


def connect(symbol: str, terminal_path: Optional[str] = None, login: Optional[int] = None,
           password: Optional[str] = None, server: Optional[str] = None) -> None:
    kwargs = {}
    if terminal_path:
        kwargs["path"] = terminal_path
    if login and password and server:
        kwargs.update(login=login, password=password, server=server)

    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")


def shutdown() -> None:
    mt5.shutdown()


def get_tick_price(symbol: str) -> tuple[float, float]:
    """Returns (bid, ask)."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
    return tick.bid, tick.ask


def price_extremes_since(symbol: str, since_msc: int) -> tuple[Optional[float], Optional[float], int]:
    """(lowest bid, highest ask, newest tick time_msc) over every tick AFTER since_msc. Touch detection
    polls about once a second, so a wick that lives for a fraction of a second falls between two
    polls; looking at the extremes of every tick since the previous look means no touch is missed
    (2026-09-21: a 0.11 s wick to 0.1 point through the H1 ATR support was never seen). Nothing OLDER
    than the previous look is included, so it can never arm a level retroactively. (None, None,
    since_msc) if there is no newer tick."""
    start = dt.datetime.fromtimestamp(since_msc / 1000.0 - 1.0, dt.timezone.utc)
    end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)
    ticks = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return None, None, since_msc
    fresh = ticks[(ticks["time_msc"] > since_msc) & (ticks["bid"] > 0) & (ticks["ask"] > 0)]
    if len(fresh) == 0:
        return None, None, since_msc
    return float(fresh["bid"].min()), float(fresh["ask"].max()), int(fresh["time_msc"].max())


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
                   comment: str = "V6S close") -> OrderResult:
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
