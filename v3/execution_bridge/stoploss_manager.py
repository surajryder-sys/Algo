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

Real bug fixed 2026-08-19: last_managed_sl only ever got refreshed
inside the branch that actually called broker.modify_position_sl, so a
manual change made while still below breakeven (or one that happened
to already match the trailing formula) was invisible to that update --
it kept looking like a FRESH manual change every single cycle, which
reset override_price_reference to whatever price happened to be right
then. In any moving market "a new high/low beyond the reference"
became trivially true within a poll or two, so the pause never
actually lasted -- confirmed live: repeatedly setting SL manually kept
getting overwritten almost immediately every time. Fixed by syncing
last_managed_sl to the real SL on every path that decides NOT to move
it this cycle, not just the one that does.
"""
from __future__ import annotations

import math
from typing import Optional

from v3.execution_bridge import broker
from v3.execution_bridge.config import Config, SourceConfig, SymbolConfig
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


def run_once(cfg: Config, source: SourceConfig, tracker: OrderTracker, sl_states: SLStateStore) -> None:
    for sym_cfg in cfg.symbols:
        symbol = sym_cfg.symbol
        tracked = tracker.get(symbol)
        if tracked is None or tracked.kind != "POSITION":
            sl_states.clear(symbol)  # nothing open -- no trailing history to keep
            continue

        try:
            _manage_one(cfg, source, sym_cfg, tracked, sl_states)
        except Exception as exc:
            print(f"[stoploss_manager:{source.name}] {symbol} ERROR: {exc}")


def _manage_one(cfg: Config, source: SourceConfig, sym_cfg: SymbolConfig, tracked, sl_states: SLStateStore) -> None:
    symbol = sym_cfg.symbol
    tag = f"[stoploss_manager:{source.name}]"
    positions = [p for p in broker.get_positions(symbol, source.magic_number) if p.ticket == tracked.ticket]
    if not positions:
        return  # disappeared -- execution_bridge.py's own _check_disappeared handles this
    position = positions[0]

    state = sl_states.get_or_reset(symbol, tracked.exec_timeframe, tracked.exec_start_time)
    direction = tracked.direction
    entry_price = position.price_open
    current_price = _current_price_for_close(symbol, direction)
    favor = _favor_points(direction, entry_price, current_price)

    # Seed the baseline from whatever the real SL already is, the very
    # first time this position is ever examined -- before this existed,
    # a manual change made before Stoploss Manager's own first trail
    # move was invisible to the override check below (see sl_state.py's
    # own docstring for the live incident that caught this).
    if not state.baseline_established:
        state.last_managed_sl = position.sl
        state.baseline_established = True
        sl_states.save()
        print(f"{tag} {symbol}: baseline SL noted ({position.sl}) -- any change from here is treated as manual")

    # Manual-override detection: the real SL no longer matches what we
    # last set ourselves.
    if state.last_managed_sl is not None and abs(position.sl - state.last_managed_sl) > 1e-6:
        if not state.manual_override_active:
            state.manual_override_active = True
            state.override_price_reference = current_price
            sl_states.save()
            print(f"{tag} {symbol}: manual SL change detected ({position.sl}) -- "
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
        # Resuming restores SL to whatever the bot had LEGITIMATELY
        # already earned before the manual change (state.last_managed_sl
        # -- never touched by the override-detection code above, so it
        # still holds that value) -- as a one-time correction, NEVER
        # straight into a further-trailed step on this same resume, even
        # if the CURRENT cycle's own favor already clears a later step.
        #
        # A first attempt at this (2026-08-22) forced the restore target
        # to entry_price (cost) unconditionally and reset
        # peak_favor_points down to trail_start_points -- a real,
        # confirmed live bug: it REGRESSED the SL backward on a trade
        # that had already trailed well past cost before the user
        # touched it (peak favor 513 points, SL legitimately at
        # entry+150), handing back profit protection that was already
        # earned. That directly violated this module's own "a proper
        # trailing stop only ever ratchets tighter, never loosens" rule
        # (see module docstring). last_managed_sl is exactly the right
        # restore target instead: it's the bot's own last legitimately
        # -computed value (whatever it actually was -- the original
        # pre-breakeven SL, cost, or a real trail step), so restoring to
        # it can never loosen anything, by construction.
        #
        # peak_favor_points itself is NOT touched here -- it already only
        # ever grows via max() and never needs correcting; capping it
        # down was the mechanism that caused the regression above. The
        # fall-through below (next cycle onward) uses the real,
        # unmodified peak_favor_points as usual, so any further trailing
        # beyond the restored point only comes from genuinely continuing
        # favorable movement, through the normal step-by-step formula.
        restore_sl = state.last_managed_sl if state.last_managed_sl is not None else entry_price
        already_there = abs((position.sl or 0.0) - restore_sl) < 1e-6
        if already_there:
            sl_states.save()
            print(f"{tag} {symbol}: new {'high' if direction == 'bull' else 'low'} reached -- "
                  f"already at the last managed SL, resuming normal trailing from here")
        elif not cfg.enable_trading:
            sl_states.save()
            print(f"{tag} {symbol}: new {'high' if direction == 'bull' else 'low'} reached -- "
                  f"WOULD restore SL to {restore_sl:.2f} -- trading disabled")
        else:
            result = broker.modify_position_sl(symbol, tracked.ticket, restore_sl)
            if result.ok:
                state.last_managed_sl = restore_sl
                sl_states.save()
                print(f"{tag} {symbol}: new {'high' if direction == 'bull' else 'low'} reached -- "
                      f"restored SL to {restore_sl:.2f}, resuming normal trailing from here")
            else:
                sl_states.save()
                print(f"{tag} {symbol}: new {'high' if direction == 'bull' else 'low'} reached but "
                      f"FAILED to restore SL to {restore_sl:.2f} -- retcode={result.retcode} {result.comment}")
        return

    state.peak_favor_points = max(state.peak_favor_points, favor)
    sl_states.save()

    desired = _desired_sl(direction, entry_price, state.peak_favor_points, sym_cfg)
    if desired is None:
        # Still below breakeven -- leave the initial SL alone. Syncing
        # last_managed_sl here (added 2026-08-19, real confirmed bug):
        # without this, a genuine manual change made while still below
        # breakeven never gets accepted as "the new normal" -- it keeps
        # looking like a FRESH manual change every single cycle (since
        # last_managed_sl never moved off the old value), which resets
        # override_price_reference to whatever price is right now each
        # time. In any moving market that makes "a new high/low beyond
        # the reference" trivially true almost immediately, so the
        # pause never lasts -- confirmed live: the user reported
        # setting SL manually repeatedly and it kept reverting within a
        # cycle or two every time.
        state.last_managed_sl = position.sl
        sl_states.save()
        return

    already_there = abs((position.sl or 0.0) - desired) < 1e-6
    if already_there:
        # Same reasoning as above -- the real SL already matches what
        # we'd want, but if it got there via a manual change that
        # happens to coincide with our own formula, last_managed_sl
        # must still be synced or the next cycle re-flags it as new.
        state.last_managed_sl = desired
        sl_states.save()
        return

    if not cfg.enable_trading:
        print(f"{tag} {symbol}: WOULD move SL to {desired:.2f} "
              f"(peak favor {state.peak_favor_points:.1f} points) -- trading disabled")
        return

    result = broker.modify_position_sl(symbol, tracked.ticket, desired)
    if result.ok:
        state.last_managed_sl = desired
        sl_states.save()
        print(f"{tag} {symbol}: moved SL to {desired:.2f} "
              f"(peak favor {state.peak_favor_points:.1f} points)")
    else:
        print(f"{tag} {symbol}: FAILED to move SL to {desired:.2f} -- "
              f"retcode={result.retcode} {result.comment}")
