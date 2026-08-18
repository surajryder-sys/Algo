"""Stoploss Manager -- point-based SL trailing for a currently open
Trend Manager position, per the user's rule 2026-08-18. Lives inside
Execution Bridge (not Signal Engine) since it needs a REAL open
position's real entry price, real current SL, and the ability to call
MT5 to modify it -- none of which Trend Manager itself ever touches.

Rule, in full (supersedes an earlier, vaguer "trail to nearest OB"
idea -- explicitly no OB-based trailing at all anymore):
- Below breakeven_points favorable movement: SL stays wherever it was
  set at entry (entries.initial_sl) -- untouched.
- >= breakeven_points: SL moves to cost (entry price).
- >= trail_start_points: SL trails in trail_step_points increments from
  there -- SL = entry +/- trail_step_points * floor((peak_favor -
  trail_start_points) / trail_step_points). E.g. defaults 7/10/2: at
  +12 points favor, SL = entry + 2.
- Based on the PEAK favorable move ever reached for this position, not
  instantaneous profit -- a proper trailing stop only ever ratchets
  tighter, never loosens if price gives back part of its move.
- If the user manually changes the real SL in MT5, Stoploss Manager
  stops touching it until price makes a genuinely NEW high (buy) / new
  low (sell) beyond the price level at the moment of that change, then
  resumes normal management from there.
"""
from __future__ import annotations

import math
from typing import Optional

from v3.execution_bridge import broker
from v3.execution_bridge.config import Config, SymbolConfig
from v3.execution_bridge.order_tracker import OrderTracker
from v3.execution_bridge.sl_state import SLStateStore


def _favor_points(direction: str, entry_price: float, current_price: float) -> float:
    return (current_price - entry_price) if direction == "bull" else (entry_price - current_price)


def _desired_sl(direction: str, entry_price: float, peak_favor: float, sym_cfg: SymbolConfig) -> Optional[float]:
    """None means "leave the initial SL alone" -- not yet at breakeven."""
    if peak_favor < sym_cfg.breakeven_points:
        return None
    if peak_favor < sym_cfg.trail_start_points:
        return entry_price
    steps = math.floor((peak_favor - sym_cfg.trail_start_points) / sym_cfg.trail_step_points)
    offset = steps * sym_cfg.trail_step_points
    return entry_price + offset if direction == "bull" else entry_price - offset


def _current_price_for_close(symbol: str, direction: str) -> float:
    """The side you'd actually close at -- a buy's floating value moves
    with bid (what you'd sell to close), a sell's with ask."""
    bid, ask = broker.get_tick_price(symbol)
    return bid if direction == "bull" else ask


def run_once(cfg: Config, tracker: OrderTracker, sl_states: SLStateStore) -> None:
    for sym_cfg in cfg.symbols:
        symbol = sym_cfg.symbol
        tracked = tracker.get(symbol)
        if tracked is None or tracked.kind != "POSITION":
            sl_states.clear(symbol)  # nothing open -- no trailing history to keep
            continue

        try:
            _manage_one(cfg, sym_cfg, tracked, sl_states)
        except Exception as exc:
            print(f"[stoploss_manager] {symbol} ERROR: {exc}")


def _manage_one(cfg: Config, sym_cfg: SymbolConfig, tracked, sl_states: SLStateStore) -> None:
    symbol = sym_cfg.symbol
    positions = [p for p in broker.get_positions(symbol, cfg.magic_number) if p.ticket == tracked.ticket]
    if not positions:
        return  # disappeared -- execution_bridge.py's own _check_disappeared handles this
    position = positions[0]

    state = sl_states.get_or_reset(symbol, tracked.exec_timeframe, tracked.exec_start_time)
    direction = tracked.direction
    entry_price = position.price_open
    current_price = _current_price_for_close(symbol, direction)
    favor = _favor_points(direction, entry_price, current_price)

    # Manual-override detection: the real SL no longer matches what we
    # last set ourselves.
    if state.last_managed_sl is not None and abs(position.sl - state.last_managed_sl) > 1e-6:
        if not state.manual_override_active:
            state.manual_override_active = True
            state.override_price_reference = current_price
            sl_states.save()
            print(f"[stoploss_manager] {symbol}: manual SL change detected ({position.sl}) -- "
                  f"pausing trailing until a new {'high' if direction == 'bull' else 'low'}")

    if state.manual_override_active:
        reference = state.override_price_reference
        made_new_extreme = (current_price > reference) if direction == "bull" else (current_price < reference)
        if not made_new_extreme:
            state.peak_favor_points = max(state.peak_favor_points, favor)
            sl_states.save()
            return  # still respecting the manual change
        state.manual_override_active = False
        state.override_price_reference = None
        print(f"[stoploss_manager] {symbol}: new {'high' if direction == 'bull' else 'low'} reached -- "
              f"resuming normal trailing")

    state.peak_favor_points = max(state.peak_favor_points, favor)
    sl_states.save()

    desired = _desired_sl(direction, entry_price, state.peak_favor_points, sym_cfg)
    if desired is None:
        return  # still below breakeven -- leave the initial SL alone

    already_there = abs((position.sl or 0.0) - desired) < 1e-6
    if already_there:
        return

    if not cfg.enable_trading:
        print(f"[stoploss_manager] {symbol}: WOULD move SL to {desired:.2f} "
              f"(peak favor {state.peak_favor_points:.1f} points) -- trading disabled")
        return

    result = broker.modify_position_sl(symbol, tracked.ticket, desired)
    if result.ok:
        state.last_managed_sl = desired
        sl_states.save()
        print(f"[stoploss_manager] {symbol}: moved SL to {desired:.2f} "
              f"(peak favor {state.peak_favor_points:.1f} points)")
    else:
        print(f"[stoploss_manager] {symbol}: FAILED to move SL to {desired:.2f} -- "
              f"retcode={result.retcode} {result.comment}")
