"""Exit Manager -- points-based partial profit booking for a currently
open Trend Manager or Reversal Manager position, per the user's rule
2026-08-26. Lives inside Execution Bridge (not Signal Engine), same
reasoning as stoploss_manager.py: needs a REAL open position's real
entry price and the ability to call MT5 to partially close it, neither
of which either Manager ever touches. Runs once per source (trend,
reversal) from execution_bridge.py's own main loop, source-agnostic --
doesn't care which Manager decided the trade, only that a real position
exists for it (same pattern Stoploss Manager already uses).

Rule, in full -- SymbolConfig.partial_tiers is the single source of
truth for the actual per-symbol numbers (see config.py's own comments
for each symbol's rationale):
- Each symbol has its own list of (points, fraction) tiers, points
  always an ABSOLUTE favorable-move distance from entry (same
  convention as Stoploss Manager's own breakeven_points/
  trail_start_points), fraction always of the symbol's own FIXED
  `lots` config value -- never of whatever volume currently remains.
  Config stores tiers pre-sorted ascending by points, so they're always
  evaluated (and can fire) in the order price actually reaches them,
  regardless of which order the user described the percentages in.
- Every tier whose point threshold the position's current peak-favor
  has reached, and that hasn't already fired for this exact position,
  books THIS cycle -- not just the next unfired one -- so a single
  large price gap that jumps straight past more than one tier in one
  poll still books all of them, not just the first.
- The FIRST tier ever booked for a position also moves its SL to
  breakeven (entry price) -- but only if that's actually tighter than
  wherever the real SL already sits (never loosens it, same defensive
  direction-aware comparison Stoploss Manager's own trailing already
  uses elsewhere). This is a one-time move, independent of whatever
  Stoploss Manager's own trailing formula separately does to the same
  SL field on its own cycle -- both are idempotent and only ever move
  SL in the favorable direction, so there's no real conflict even
  though they're two separate mechanisms touching the same value (see
  config.py's own USTEC comment for a case where this one fires
  first).
- Whatever fraction is left over after every configured tier (e.g.
  XAUUSD's remaining 25%) is never touched by this module at all --
  it rides on Stoploss Manager's own trailing (or the source Manager's
  own bias-flip/invalidation close) exactly as if Exit Manager didn't
  exist.
- A symbol with no partial_tiers configured (empty tuple) is a
  complete no-op here -- unaffected, same as before this module
  existed.
"""
from __future__ import annotations

from typing import Optional

from v3.execution_bridge import broker
from v3.execution_bridge.config import Config, SourceConfig, SymbolConfig
from v3.execution_bridge.exit_state import ExitStateStore
from v3.execution_bridge.order_tracker import OrderTracker


def _favor_points(direction: str, entry_price: float, current_price: float) -> float:
    """Mirrors stoploss_manager._favor_points exactly -- kept as its
    own tiny local copy rather than a cross-module import of another
    file's private helper, same "small enough to duplicate with a
    pointer comment" convention already used elsewhere in this
    codebase (e.g. reversal_manager.py's own _formation_trusted)."""
    return (current_price - entry_price) if direction == "bull" else (entry_price - current_price)


def _current_price_for_close(symbol: str, direction: str) -> float:
    """Mirrors stoploss_manager._current_price_for_close exactly --
    the side you'd actually close at (a buy's floating value moves with
    bid, a sell's with ask)."""
    bid, ask = broker.get_tick_price(symbol)
    return bid if direction == "bull" else ask


def run_once(cfg: Config, source: SourceConfig, tracker: OrderTracker, exit_states: ExitStateStore) -> None:
    for sym_cfg in cfg.symbols:
        symbol = sym_cfg.symbol
        if not sym_cfg.partial_tiers:
            continue  # no partial-booking rule configured for this symbol at all
        tracked = tracker.get(symbol)
        if tracked is None or tracked.kind != "POSITION":
            exit_states.clear(symbol)  # nothing open -- no booking history to keep
            continue

        try:
            _manage_one(cfg, source, sym_cfg, tracked, exit_states)
        except Exception as exc:
            print(f"[exit_manager:{source.name}] {symbol} ERROR: {exc}")


def _manage_one(cfg: Config, source: SourceConfig, sym_cfg: SymbolConfig, tracked, exit_states: ExitStateStore) -> None:
    symbol = sym_cfg.symbol
    tag = f"[exit_manager:{source.name}]"
    positions = [p for p in broker.get_positions(symbol, source.magic_number) if p.ticket == tracked.ticket]
    if not positions:
        return  # disappeared -- execution_bridge.py's own _check_disappeared handles this
    position = positions[0]

    state = exit_states.get_or_reset(symbol, tracked.exec_timeframe, tracked.exec_start_time)
    direction = tracked.direction
    entry_price = position.price_open
    current_price = _current_price_for_close(symbol, direction)
    favor = _favor_points(direction, entry_price, current_price)

    due = [
        (points, fraction) for points, fraction in sym_cfg.partial_tiers
        if favor >= points and points not in state.booked_tier_points
    ]
    if not due:
        return

    for points, fraction in due:
        if position.volume <= 0:
            break  # fully closed by an earlier tier this same cycle -- nothing left to book further

        raw_volume = sym_cfg.lots * fraction
        volume = broker.round_volume(symbol, min(raw_volume, position.volume))
        if volume is None:
            print(f"{tag} {symbol}: partial-book tier @ {points:g}pts ({fraction:.0%}) wants "
                  f"{raw_volume:.4f} lots -- below broker's own volume_min after rounding, skipping "
                  f"this cycle (will retry next poll)")
            continue

        if not cfg.enable_trading:
            print(f"{tag} {symbol}: WOULD partial-close {volume} lots (tier @ {points:g}pts, "
                  f"{fraction:.0%} of {sym_cfg.lots} lots) -- trading disabled")
            continue

        result = broker.close_partial_position(symbol, position, volume, cfg.deviation_points,
                                                 f"V3-{source.comment_prefix}-partial")
        if not result.ok:
            print(f"{tag} {symbol}: FAILED to partial-close {volume} lots (tier @ {points:g}pts) -- "
                  f"retcode={result.retcode} {result.comment}")
            continue

        state.booked_tier_points.append(points)
        exit_states.save()
        print(f"{tag} {symbol}: partial-closed {volume} lots (tier @ {points:g}pts, {fraction:.0%} "
              f"of {sym_cfg.lots} lots) -- {result}")
        position.volume = max(position.volume - volume, 0.0)  # local bookkeeping for any further tier this cycle

        if not state.breakeven_applied:
            _apply_breakeven_once(tag, symbol, direction, entry_price, position, tracked, cfg, state, exit_states)


def _apply_breakeven_once(tag: str, symbol: str, direction: str, entry_price: float, position,
                           tracked, cfg: Config, state, exit_states: ExitStateStore) -> None:
    """One-time move to breakeven on the FIRST partial booking for this
    position -- see this module's own docstring for why this can
    coexist with Stoploss Manager's own separate trailing untouched.
    Marks breakeven_applied regardless of whether an actual SL move
    happened (already at or past breakeven counts as "done", not
    "pending retry")."""
    current_sl = position.sl or 0.0
    already_better_or_equal = (
        current_sl >= entry_price - 1e-9 if direction == "bull" else
        (current_sl > 0.0 and current_sl <= entry_price + 1e-9)
    )
    state.breakeven_applied = True
    exit_states.save()
    if already_better_or_equal:
        return  # Stoploss Manager (or a manual change) already got here first -- nothing to do

    if not cfg.enable_trading:
        print(f"{tag} {symbol}: WOULD move SL to breakeven ({entry_price:.2f}) after first partial booking "
              f"-- trading disabled")
        return

    result = broker.modify_position_sl(symbol, tracked.ticket, entry_price, position.tp)
    if result.ok:
        print(f"{tag} {symbol}: moved SL to breakeven ({entry_price:.2f}) after first partial booking")
    else:
        print(f"{tag} {symbol}: FAILED to move SL to breakeven ({entry_price:.2f}) -- "
              f"retcode={result.retcode} {result.comment}")
