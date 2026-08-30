"""Thin MT5 wrapper for the crypto Trend Manager -- every function takes a
symbol parameter since one process/one connection handles both BTCUSD and
ETHUSD (see config.py's own docstring for why). Own copy, not shared with
v4/trend_manager/broker.py -- same per-bot isolation convention as
everywhere else in this repo, even though the two are structurally
similar.
"""
from __future__ import annotations

from typing import Optional

import MetaTrader5 as mt5

from v4.crypto_trend_manager.config import Config


def connect(cfg: Config) -> None:
    kwargs = {}
    if cfg.mt5_terminal_path:
        kwargs["path"] = cfg.mt5_terminal_path
    if cfg.mt5_login and cfg.mt5_password and cfg.mt5_server:
        kwargs.update(login=cfg.mt5_login, password=cfg.mt5_password, server=cfg.mt5_server)

    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    for symbol in ("BTCUSD", "ETHUSD"):
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
    """The single open crypto-Trend-Manager position (own magic number)
    for this symbol, or None -- one position per symbol at a time
    (netting account; an opposite-direction order closes/reverses it)."""
    positions = mt5.positions_get(symbol=symbol) or ()
    for p in positions:
        if p.magic == cfg.magic_number:
            return p
    return None


def position_direction(cfg: Config, symbol: str) -> Optional[str]:
    """"buy" | "sell" | None (flat) -- convenience wrapper used constantly
    by the cross-symbol gating logic in engine.py."""
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
    """Flattens this symbol's current crypto-Trend-Manager position with an
    opposite-direction deal for its FULL volume -- used only by the
    cross-symbol gating rule (BTCUSD's open position closing ETHUSD's
    opposing one proactively, "close sell and wait for buy setup"), not by
    ordinary same-engine reversals (those just net via send_market_order
    like everywhere else in this repo). No-op (returns None) if nothing is
    actually open."""
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


def modify_sl(cfg: Config, symbol: str, ticket: int, new_sl: float):
    """Moves the SL on an existing open position -- TRADE_ACTION_SLTP, not
    a new order. Caller (exit_manager.py, via main.py) is responsible for
    only calling this when the new SL is actually tighter (never loosens)
    -- that check lives at the call site, not buried here.

    Explicitly re-sends the position's OWN current comment on every SLTP
    request (2026-08-30, user's explicit request: "open position comment
    should always remain same") -- this fires every poll once the
    continuous step-trail is active (far more often than a one-time tier
    partial-close), so if a broker/platform ever treats an omitted
    comment field as "clear it" rather than "leave unchanged," this would
    be the dominant, most frequent source of the open position's own
    entry comment (e.g. V4S-M30-STR-M5-STR-<ts>) getting overwritten --
    passing it back unchanged on every call removes that risk entirely,
    regardless of which specific behavior the broker actually has."""
    position = mt5.positions_get(ticket=ticket)
    if not position:
        raise RuntimeError(f"No position found for ticket {ticket}")
    p = position[0]
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": new_sl,
        "tp": p.tp,
        "comment": p.comment,
    }
    return mt5.order_send(request)


def partial_close(cfg: Config, symbol: str, ticket: int, direction: str, volume: float, comment: str):
    """Closes PART of an existing position -- a real deal in the OPPOSITE
    direction, same symbol, tagged to this position's ticket, for exactly
    `volume` (less than the position's full size). Netting-account
    mechanics do the rest, same as close_position above."""
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
