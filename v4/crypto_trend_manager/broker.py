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


def modify_sl(cfg: Config, symbol: str, ticket: int, new_sl: float, comment: Optional[str] = None):
    """Moves the SL on an existing open position -- TRADE_ACTION_SLTP, not
    a new order. Caller (exit_manager.py, via main.py) is responsible for
    only calling this when the new SL is actually tighter (never loosens)
    -- that check lives at the call site, not buried here.

    Explicitly sends a comment on every SLTP request rather than omitting
    the field -- 2026-08-30, user's explicit request: "open position
    comment should always remain same". Defaults to the position's OWN
    current comment (used for ordinary trailing-SL updates, which fire
    every poll once the step-trail is active); callers doing a same-poll
    comment RESTORE after a partial close (see main.py's own use of this)
    pass the entry's original comment explicitly instead, since by that
    point the position's own .comment has already been overwritten by the
    partial-close deal's comment (confirmed live: this broker propagates
    a partial-close deal's comment onto the leftover open position) and
    re-reading it here would just re-send the wrong value.

    Confirmed live, 2026-08-31: every single comment-restore call up to
    this fix had SILENTLY FAILED (retcode 10025, "No changes") -- MT5
    rejects a TRADE_ACTION_SLTP request outright when neither sl nor tp
    numerically differs from the position's current values, REGARDLESS of
    the comment field differing. This bites constantly in practice: the
    continuous step-trail routinely reaches a tier's SL level before that
    tier's own partial close fires, so by the time the restore call runs,
    SL is already exactly where it needs to be -- "nothing to change" as
    far as MT5 is concerned, comment included. If new_sl would be a pure
    no-op, nudge it by one symbol point (the smallest real price
    increment MT5 will accept as an actual change) in the FAVORABLE
    direction -- still never loosens (a tighter-by-one-tick SL is, if
    anything, marginally MORE protective, never less), but is now a
    genuine numeric change MT5 will actually process, comment included."""
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
