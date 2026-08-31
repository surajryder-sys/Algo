"""Thin MT5 wrapper for the USOIL/USTEC Trend Manager -- every function
takes a symbol parameter since one process/one connection handles both
(see config.py's own docstring for why). Own copy, not shared with
crypto_trend_manager/broker.py or v4/trend_manager/broker.py -- same
per-bot isolation convention as everywhere else in this repo.
"""
from __future__ import annotations

from typing import Optional

import MetaTrader5 as mt5

from v4.usoil_ustec_trend_manager.config import Config, SYMBOLS


def connect(cfg: Config) -> None:
    kwargs = {}
    if cfg.mt5_terminal_path:
        kwargs["path"] = cfg.mt5_terminal_path
    if cfg.mt5_login and cfg.mt5_password and cfg.mt5_server:
        kwargs.update(login=cfg.mt5_login, password=cfg.mt5_password, server=cfg.mt5_server)

    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    for symbol in SYMBOLS:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")


def shutdown() -> None:
    mt5.shutdown()


def get_mid_price(symbol: str) -> float:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
    return (tick.bid + tick.ask) / 2.0


def get_position(cfg: Config, symbol: str):
    """The single open USOIL/USTEC-Trend-Manager position (own magic
    number) for this symbol, or None -- one position per symbol at a
    time (netting-manual, see send_market_order/close_position's own
    docstrings for why full close-then-open is needed on this hedging
    account)."""
    positions = mt5.positions_get(symbol=symbol) or ()
    for p in positions:
        if p.magic == cfg.magic_number:
            return p
    return None


def position_direction(cfg: Config, symbol: str) -> Optional[str]:
    p = get_position(cfg, symbol)
    if p is None:
        return None
    return "buy" if p.type == mt5.ORDER_TYPE_BUY else "sell"


def send_market_order(cfg: Config, symbol: str, direction: str, sl: float, comment: str):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")

    order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "buy" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": cfg.lot_sizes[symbol],
        "type": order_type,
        "price": price,
        "sl": sl,
        "magic": cfg.magic_number,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return mt5.order_send(request)


def close_position(cfg: Config, symbol: str, comment: str):
    """Flattens this symbol's current position with an opposite-direction
    deal for its FULL volume -- confirmed live (crypto_trend_manager,
    2026-08-30) this account is RETAIL_HEDGING, not netting, so an
    opposite order does NOT auto-close/reverse an existing position; this
    explicit close is required before any reversal fire. No-op if nothing
    is actually open."""
    p = get_position(cfg, symbol)
    if p is None:
        return None

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")

    closing_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price = tick.bid if closing_type == mt5.ORDER_TYPE_SELL else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": p.volume,
        "type": closing_type,
        "position": p.ticket,
        "price": price,
        "magic": cfg.magic_number,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return mt5.order_send(request)


def modify_sl(cfg: Config, symbol: str, ticket: int, new_sl: float, comment: Optional[str] = None):
    """Moves the SL on an existing open position -- TRADE_ACTION_SLTP, not
    a new order. Always sends an explicit comment (default: the
    position's own current one) rather than omitting the field, same
    fix crypto_trend_manager's own modify_sl applies -- confirmed live
    there that omitting it, or letting a partial-close deal's comment
    propagate, can overwrite the open position's displayed comment.

    Confirmed live, 2026-08-31 (same incident, found via USTEC): every
    comment-restore call up to this fix had SILENTLY FAILED (retcode
    10025, "No changes") -- MT5 rejects TRADE_ACTION_SLTP outright when
    neither sl nor tp numerically differs from the position's current
    values, regardless of the comment differing. The continuous step-
    trail routinely reaches a tier's SL level before that tier's own
    partial close fires, so the restore call's SL is often already a
    no-op by the time it runs. If new_sl would be a pure no-op, nudge it
    by one symbol point (the smallest real price increment) in the
    FAVORABLE direction -- still never loosens, but is now a genuine
    numeric change MT5 will actually process, comment included."""
    position = mt5.positions_get(ticket=ticket)
    if not position:
        raise RuntimeError(f"No position found for ticket {ticket}")
    p = position[0]

    if new_sl == p.sl:
        info = mt5.symbol_info(symbol)
        point = info.point if info is not None else 0.0
        if point:
            new_sl = new_sl + point if p.type == mt5.ORDER_TYPE_BUY else new_sl - point

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": new_sl,
        "tp": p.tp,
        "comment": comment if comment is not None else p.comment,
    }
    return mt5.order_send(request)


def partial_close(cfg: Config, symbol: str, ticket: int, direction: str, volume: float, comment: str):
    """Closes PART of an existing position -- a real deal in the OPPOSITE
    direction, same symbol, tagged to this position's ticket."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")

    order_type = mt5.ORDER_TYPE_SELL if direction == "buy" else mt5.ORDER_TYPE_BUY
    price = tick.bid if direction == "buy" else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "position": ticket,
        "price": price,
        "magic": cfg.magic_number,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return mt5.order_send(request)
