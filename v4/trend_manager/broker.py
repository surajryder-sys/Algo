"""Thin MT5 wrapper for V4's Trend Manager -- connection, tick price, and
recent M1 bar history (to find the candle immediately before a given
flip's structure_event_time). Same shape as algo_v2/broker.py, kept as
V4's own copy per this repo's per-bot isolation convention -- no strategy
logic lives here, just MT5 plumbing.
"""
from __future__ import annotations

from typing import Optional

import MetaTrader5 as mt5

from v4.trend_manager.config import Config

# Generous enough to find "the bar before" a flip even if a few polls'
# worth of delay passed before this ran -- cheap, one bounded fetch.
_M1_HISTORY_BARS = 30


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


def has_open_position(cfg: Config) -> bool:
    """True if a REAL position under V4's own magic number currently
    exists for this symbol -- used to reconcile V4ExecutionState against
    reality every poll (see that class's own reconcile() docstring for
    the real bug this fixes: a position closing via SL previously left
    tracked state stuck forever, silently swallowing the next genuine
    same-direction flip)."""
    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    return any(p.magic == cfg.magic_number for p in positions)


def get_mid_price(symbol: str) -> float:
    bid, ask = get_tick_price(symbol)
    return (bid + ask) / 2.0


def send_market_order(cfg: Config, direction: str, sl: float, comment: str) -> "mt5.OrderSendResult":
    """Places a real market order -- BUY or SELL -- with the given SL, V4's
    own magic number/lot size, and comment. Caller (main.py) is
    responsible for checking cfg.enable_trading before ever calling this;
    this function itself does not gate on that, same separation
    algo_v2/broker.py's own order-send functions use (the safety check
    lives at the call site, not buried in the plumbing)."""
    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {cfg.symbol}: {mt5.last_error()}")

    order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "buy" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": cfg.symbol,
        "volume": cfg.lot_size,
        "type": order_type,
        "price": price,
        "sl": sl,
        "magic": cfg.magic_number,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return mt5.order_send(request)


def get_position(cfg: Config):
    """The single open V4 position (own magic number) for this symbol, or
    None. V4 only ever holds one position at a time (netting account, one
    direction) -- see has_open_position's own docstring."""
    positions = mt5.positions_get(symbol=cfg.symbol) or ()
    for p in positions:
        if p.magic == cfg.magic_number:
            return p
    return None


def modify_sl(cfg: Config, ticket: int, new_sl: float) -> "mt5.OrderSendResult":
    """Moves the SL on an existing open position -- TRADE_ACTION_SLTP,
    not a new order. Caller is responsible for only calling this when the
    new SL is actually tighter/more favorable (never loosens) -- same
    defensive direction-aware comparison v3's own Stoploss Manager uses,
    kept at the call site (exit_manager.py) not buried here."""
    position = mt5.positions_get(ticket=ticket)
    if not position:
        raise RuntimeError(f"No position found for ticket {ticket}")
    p = position[0]
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": cfg.symbol,
        "position": ticket,
        "sl": new_sl,
        "tp": p.tp,
    }
    return mt5.order_send(request)


def partial_close(cfg: Config, ticket: int, direction: str, volume: float, comment: str) -> "mt5.OrderSendResult":
    """Closes PART of an existing position -- a real deal in the OPPOSITE
    direction, same symbol, tagged to this position's ticket, for exactly
    `volume` (less than the position's full size). Netting-account
    mechanics do the rest (same automatic net-reduction behavior already
    relied on for full closes elsewhere in this module)."""
    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {cfg.symbol}: {mt5.last_error()}")

    # Opposite of the position's own direction closes (reduces) it.
    order_type = mt5.ORDER_TYPE_SELL if direction == "buy" else mt5.ORDER_TYPE_BUY
    price = tick.bid if direction == "buy" else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": cfg.symbol,
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


def find_previous_candle_close(symbol: str, structure_event_time: int) -> Optional[float]:
    """The close of the M1 bar AT structure_event_time itself -- i.e. the
    flip candle's own close, which the 5-point edge-gap filter is
    measured from. Named "previous" because it's the most recently
    CLOSED candle relative to whatever is live/forming now, not because
    it's one bar earlier than the flip -- confirmed live 2026-08-28 this
    was fetching the WRONG bar (one before the flip, e.g. 4540.03) instead
    of the flip candle's own close (e.g. 4530.62, what the user actually
    saw on the real chart) -- "the candle close is the candle which
    closes above or below the lines" IS the flip candle. Returns None if
    that bar isn't in the fetched window (e.g. a very stale event_time,
    or MT5 history not yet synced that far back) -- callers should treat
    that the same as "can't confirm the gap," not assume it passes."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, _M1_HISTORY_BARS)
    if rates is None:
        return None
    for r in rates:
        if int(r["time"]) == structure_event_time:
            return float(r["close"])
    return None
