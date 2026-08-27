"""Execution Bridge main loop -- see v3/execution_bridge/__init__.py for
what this is. Reconciles real MT5 pending orders/positions against
whatever each SOURCE has already decided -- Trend Manager
(trend_manager_trade_state.json) and Reversal Manager
(reversal_manager_state.json), each read-only, each its own magic
number/comment prefix/order tracking (see config.py's SourceConfig).
The two sources are reconciled fully independently every cycle -- they
can each hold a position on the same symbol at the same time (confirmed
2026-08-17), and neither ever knows about the other's tickets. Never
decides direction/entry/SL itself.

Each cycle, per (source, symbol):
1. If Execution Bridge is already tracking a real ticket for this
   source+symbol, check whether it's disappeared from MT5 since last
   poll -- if so, work out why (filled, manually cancelled/closed,
   SL/TP hit, or Execution Bridge's own expected cancellation) via
   intervention.py, relay a manual event to the SOURCE'S OWN manual-
   events consumer if that's what happened, then drop the stale
   tracking. (Both sources read their own manual-events file as of
   2026-08-18 -- stale note here previously said only Trend Manager
   did; Reversal Manager's own relay was added the same day a real SL
   hit left its state stuck FILLED forever, see SourceConfig's own
   comment in config.py.)
2. Compare the source's desired state against what's now tracked --
   same shape for both sources (direction/status/exec_timeframe/
   exec_start_time/mode/entry_price/sl_price), Reversal Manager's own
   record normalized the same way Trend Manager's already is:
   - Nothing desired (no active trade, or Trend Manager's own
     AWAITING_TRIGGER) but something's tracked -> cancel/close it.
   - PENDING desired, tracked doesn't match -> cancel old, place new.
   - PENDING desired, nothing tracked -> place it.
   - FILLED/MARKET desired, nothing tracked -> place a market order now.
   - FILLED/PENDING desired -> look for the real position the broker
     should have already filled it into on its own.

   Step 2 is SKIPPED entirely for a symbol on any cycle where step 1
   just found and cleared a disappearance (added 2026-08-19) -- gives
   the source Manager at least one cycle to react before step 2 trusts
   its state again. On its own this was NOT sufficient (see below), but
   still prevents acting on a snapshot step 1 already proved stale in
   the exact same breath.

Two further real, live incidents (2026-08-19, see _check_disappeared's
own docstring for the full detail) needed dedicated fixes beyond the
skip above, since the skip only ever buys one cycle (2s) and neither
source's own reaction is guaranteed that fast:
1. A filled pending order got duplicated up to 4x in ~30s -- Trend
   Manager's own PENDING->FILLED transition took longer than one
   skipped cycle, so each subsequent cycle still saw "PENDING desired,
   nothing tracked" and placed another one. Fixed at the root: the
   PENDING branch now also checks for an already-filled matching
   POSITION (not just a matching pending order) before placing
   anything new, and adopts it if found -- this doesn't depend on
   either source's state-transition speed at all.
2. A user's manual position close was reopened 5s later -- Reversal
   Manager's own 5s poll interval hadn't caught up by the very next
   Execution Bridge cycle. Fixed with an explicit cooldown
   (_MANUAL_CLOSE_COOLDOWN_SECONDS, 8s): after a manual close/cancel or
   a genuine SL/TP hit is relayed, Execution Bridge refuses to
   re-place THAT EXACT (exec_timeframe, exec_start_time) trade again
   until the cooldown expires -- a genuinely different/newer setup the
   source proposes in the meantime is NOT blocked.

Run with: python -m v3.execution_bridge.execution_bridge
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from v3.execution_bridge import broker, exit_manager, intervention, manual_events, stoploss_manager
from v3.execution_bridge.config import Config, SourceConfig, SymbolConfig, load_config
from v3.execution_bridge.exit_state import ExitStateStore
from v3.execution_bridge.order_tracker import OrderTracker, TrackedOrder, make_comment
from v3.execution_bridge.sl_state import SLStateStore

# How long, after a manual close/cancel (or a genuine SL/TP hit) is
# relayed to a source, Execution Bridge refuses to re-place THAT EXACT
# (exec_timeframe, exec_start_time) trade again -- even past the
# one-cycle reconcile-skip below. Added 2026-08-19 after the skip alone
# proved insufficient: confirmed live, a manual ETHUSD close was
# reopened 5 seconds later anyway, because Reversal Manager's own state
# (polls every 5s) hadn't caught up by the very next Execution Bridge
# cycle (polls every 2s) -- one skipped cycle bought 2 seconds, not
# enough. 8s comfortably covers either source's 5s poll interval plus
# processing/write time. Keyed by (source_name, symbol); does NOT block
# a genuinely different/newer setup the source proposes during the
# window -- only a re-proposal of the SAME trade just closed.
_MANUAL_CLOSE_COOLDOWN_SECONDS = 8.0
_cooldowns: dict[tuple[str, str], tuple[str, int, float]] = {}


def _start_cooldown(source: SourceConfig, symbol: str, exec_timeframe: str, exec_start_time: int) -> None:
    _cooldowns[(source.name, symbol)] = (exec_timeframe, exec_start_time, time.time() + _MANUAL_CLOSE_COOLDOWN_SECONDS)


def _cooldown_blocks(source: SourceConfig, symbol: str, exec_timeframe: str, exec_start_time: int) -> bool:
    entry = _cooldowns.get((source.name, symbol))
    if entry is None:
        return False
    cd_timeframe, cd_start_time, expires_at = entry
    if time.time() >= expires_at:
        del _cooldowns[(source.name, symbol)]
        return False
    return (cd_timeframe, cd_start_time) == (exec_timeframe, exec_start_time)


def _read_desired_state(path: str) -> dict:
    """Reads a Manager's own "active_trades" dict, read-only. Same
    shape expected from every source; Reversal Manager's own status
    already normalized to AWAITING_TRIGGER/PENDING/FILLED by
    reversal_tracker.py, matching Trend Manager's own convention."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return raw.get("active_trades", {})


def _find_matching_pending(symbol: str, magic: int, comment: str):
    for order in broker.get_pending_orders(symbol, magic):
        if order.comment == comment:
            return order
    return None


def _find_matching_position(symbol: str, magic: int, comment: str):
    for position in broker.get_positions(symbol, magic):
        if position.comment == comment:
            return position
    return None


def _cancel_or_close_tracked(cfg: Config, source: SourceConfig, tracker: OrderTracker, symbol: str,
                              tracked: TrackedOrder, reason: str) -> None:
    tag = f"[execution_bridge:{source.name}]"
    if not cfg.enable_trading:
        print(f"{tag} {symbol}: WOULD cancel/close ticket={tracked.ticket} ({reason}) -- trading disabled")
        tracker.clear(symbol)
        return

    if tracked.kind == "PENDING":
        tracker.mark_expected_cancellation(tracked.ticket)  # before cancelling -- see order_tracker.py
        result = broker.cancel_pending_order(tracked.ticket)
        print(f"{tag} {symbol}: cancelled pending {tracked.ticket} ({reason}) -- {result}")
        if not result.ok:
            # Keep tracking it -- confirmed live 2026-08-18: clearing
            # unconditionally here meant a FAILED cancel/close still
            # made Execution Bridge forget the ticket entirely, so
            # nothing ever retried and a real position stayed stuck
            # open with no further attempt to close it. Only forget on
            # actual success now.
            return
    else:
        positions = [p for p in broker.get_positions(symbol, source.magic_number) if p.ticket == tracked.ticket]
        if not positions:
            tracker.clear(symbol)  # genuinely gone already -- nothing to retry
            return
        # Short, fixed comment -- MT5 rejects close requests whose
        # comment exceeds its ~31-char limit ("Invalid comment" error,
        # confirmed live 2026-08-18: embedding the full free-text
        # reason triggered this). Reason stays in the console log line
        # below, never in the comment itself.
        result = broker.close_position(symbol, positions[0], cfg.deviation_points,
                                        f"V3-{source.comment_prefix}-close")
        print(f"{tag} {symbol}: closed position {tracked.ticket} ({reason}) -- {result}")
        if not result.ok:
            return  # keep tracking it -- see the PENDING branch's own comment above
    tracker.clear(symbol)


def _check_disappeared(cfg: Config, source: SourceConfig, tracker: OrderTracker, symbol: str) -> bool:
    """If Execution Bridge is tracking a ticket that's no longer open in
    MT5, works out why and clears the stale tracking. Relays a manual
    event to Trend Manager's own consumer if this source is "trend"
    (Reversal Manager doesn't have that feedback loop yet). Mutates
    tracker; does not place anything new (that's _reconcile's job).

    Returns True if a disappearance was found and tracking cleared --
    run_once uses this to SKIP _reconcile for this symbol for the rest
    of this cycle. Without that, _reconcile would immediately act on the
    same desired_state snapshot this cycle already read at the top of
    run_once, before the source Manager had any chance to react to what
    was JUST discovered here -- confirmed live 2026-08-19, twice, both
    against real filled orders:
    1. A pending order filled, cleared here, then _reconcile (same
       cycle, desired_state still said PENDING) placed a brand new
       pending order at the same price -- which also filled instantly,
       repeating up to 4x in a row until the source's own state file
       finally caught up. Left 3 duplicate, completely untracked real
       positions open (order_tracker only ever holds one ticket).
    2. The user manually closed a real position; detected and relayed
       here correctly, but _reconcile (same cycle, desired_state still
       said FILLED) immediately reopened the exact same trade one
       second later -- undoing the manual close entirely. It only
       self-corrected because Trend Manager happened to invalidate the
       setup on its own very next poll; nothing guaranteed that.
    """
    tag = f"[execution_bridge:{source.name}]"
    tracked = tracker.get(symbol)
    if tracked is None:
        return False

    if tracked.kind == "PENDING":
        still_there = any(o.ticket == tracked.ticket for o in broker.get_pending_orders(symbol, source.magic_number))
        if still_there:
            return False
        outcome = intervention.check_pending_disappeared(tracked.ticket, tracker)
        if outcome == "manual":
            _start_cooldown(source, symbol, tracked.exec_timeframe, tracked.exec_start_time)
            if source.manual_events_file:
                manual_events.write_event(source.manual_events_file, symbol,
                                           tracked.exec_timeframe, tracked.exec_start_time)
                print(f"{tag} {symbol}: pending order {tracked.ticket} manually cancelled -- notified {source.name}")
            else:
                print(f"{tag} {symbol}: pending order {tracked.ticket} manually cancelled -- "
                      f"{source.name} has no relay configured, WILL NOT be notified")
        elif outcome == "filled":
            print(f"{tag} {symbol}: pending order {tracked.ticket} filled")
        tracker.clear(symbol)  # stale either way -- a fill gets re-discovered as a position next cycle
        return True
    else:
        still_there = any(p.ticket == tracked.ticket for p in broker.get_positions(symbol, source.magic_number))
        if still_there:
            return False
        outcome = intervention.check_position_disappeared(tracked.ticket)
        # Manual close AND a genuine SL/TP hit all mean the same thing
        # from the source Manager's own point of view: this trade is
        # over in reality, close and permanently block it -- confirmed
        # live 2026-08-18: Trend Manager's own state has NO other way
        # to learn a real SL hit happened (its only closure signal is
        # the OB itself getting mitigated on the chart, a completely
        # separate thing), so a stopped-out position left its
        # active_trade record showing FILLED forever with nothing real
        # behind it. Only a bot-initiated close (DEAL_REASON_EXPERT,
        # i.e. Execution Bridge's own flip/no-longer-desired close)
        # does NOT relay -- the source already knows about that one,
        # it asked for it.
        if outcome in ("manual", "sl", "tp"):
            _start_cooldown(source, symbol, tracked.exec_timeframe, tracked.exec_start_time)
            if source.manual_events_file:
                manual_events.write_event(source.manual_events_file, symbol,
                                           tracked.exec_timeframe, tracked.exec_start_time)
                print(f"{tag} {symbol}: position {tracked.ticket} closed ({outcome}) -- notified {source.name}")
            else:
                # Confirmed live 2026-08-18: this used to say "notified"
                # unconditionally even when there was no file to write
                # to -- misleading. A source with no manual_events_file
                # has NO way to learn about this close at all.
                print(f"{tag} {symbol}: position {tracked.ticket} closed ({outcome}) -- "
                      f"{source.name} has no relay configured, WILL NOT be notified")
        tracker.clear(symbol)
        return True


def _reconcile(cfg: Config, source: SourceConfig, tracker: OrderTracker, sym_cfg: SymbolConfig,
               desired: Optional[dict]) -> None:
    tag = f"[execution_bridge:{source.name}]"
    symbol = sym_cfg.symbol
    magic = source.magic_number
    tracked = tracker.get(symbol)

    if desired is None or desired.get("status") == "AWAITING_TRIGGER":
        if tracked is not None:
            _cancel_or_close_tracked(cfg, source, tracker, symbol, tracked, "no longer desired")
        return

    direction = desired["direction"]
    status = desired["status"]
    exec_timeframe = desired.get("exec_timeframe") or desired.get("entry_timeframe")
    exec_start_time = desired.get("exec_start_time") or desired.get("entry_start_time")
    # parent_timeframe: Trend Manager's ActiveTrade always has one.
    # Reversal Manager's ActiveReversalTrade only got the field added
    # 2026-08-20 (see reversal_tracker.py) -- falls back to exec_timeframe
    # for the rare case it's still missing (M5-immediate fires, where
    # parent and exec are genuinely the same zone anyway) rather than
    # leaving the comment malformed.
    parent_timeframe = desired.get("parent_timeframe") or exec_timeframe
    comment = (make_comment(source.comment_prefix, parent_timeframe, exec_timeframe, direction, exec_start_time)
               if exec_timeframe else None)

    if status == "PENDING":
        # Only supersede a still-RESTING pending order this way -- never
        # a real, already-filled POSITION. Real bug, confirmed live
        # 2026-08-27: the source's own PENDING->FILLED transition can lag
        # a real fill by more than one cycle (same root cause the
        # FILLED-branch's own identical guard below was already added
        # for, 2026-08-20) -- if a FRESHER trigger candidate replaces the
        # source's own (still-PENDING-in-its-own-bookkeeping) proposal
        # before that catch-up happens, this used to close the real,
        # already-open, possibly-profitable position just to chase the
        # new candidate ("superseded by a better setup" on a real
        # position -- XAUUSD lost a live +37.36 position to this exact
        # sequence). Now leaves the real position tracked and untouched
        # when this happens -- Stoploss Manager/Exit Manager keep
        # managing it normally (both key off this tracker, not the
        # source's own active-trade identity), it just won't get an
        # early close from the source's own invalidation logic anymore
        # (that logic no longer considers this ticket "its" trade either,
        # once its own state moved on) -- SL/TP and manual close still
        # work exactly as before, and _check_disappeared's own unconditional
        # per-cycle check cleans up this tracker the moment the real
        # position actually closes, letting the newer candidate get
        # placed fresh from there.
        if tracked is not None and tracked.kind == "PENDING" and \
                (tracked.exec_timeframe, tracked.exec_start_time) != (exec_timeframe, exec_start_time):
            _cancel_or_close_tracked(cfg, source, tracker, symbol, tracked, "superseded by a better setup")
            tracked = None
        elif tracked is not None and tracked.kind == "POSITION" and \
                (tracked.exec_timeframe, tracked.exec_start_time) != (exec_timeframe, exec_start_time):
            print(f"{tag} {symbol}: source proposes a different PENDING setup, but ticket {tracked.ticket} "
                  f"is already a real filled position -- leaving it open, not superseding it")
            return

        if tracked is None:
            existing = _find_matching_pending(symbol, magic, comment)
            if existing is not None:
                tracker.set(symbol, TrackedOrder("PENDING", existing.ticket, direction, exec_timeframe, exec_start_time))
                print(f"{tag} {symbol}: reconciled existing pending order {existing.ticket}")
                return
            # The pending order this same comment describes may already
            # have FILLED into a real position -- the source's own state
            # can lag the real fill by more than one Execution Bridge
            # cycle (confirmed live 2026-08-19: XAUUSD duplicated 4x in
            # ~30s because Trend Manager's PENDING->FILLED transition
            # took longer than the one-cycle reconcile-skip covers).
            # Checking for a matching POSITION here, not just a matching
            # pending order, fixes this at the root regardless of how
            # slow either source is to update its own state.
            existing_position = _find_matching_position(symbol, magic, comment)
            if existing_position is not None:
                tracker.set(symbol, TrackedOrder("POSITION", existing_position.ticket, direction,
                                                  exec_timeframe, exec_start_time))
                print(f"{tag} {symbol}: reconciled already-filled position {existing_position.ticket} "
                      f"(source still says PENDING)")
                return
            if _cooldown_blocks(source, symbol, exec_timeframe, exec_start_time):
                print(f"{tag} {symbol}: still in post-manual-close cooldown -- not re-placing yet")
                return
            if not cfg.enable_trading:
                print(f"{tag} {symbol}: WOULD place {direction} LIMIT @ {desired['entry_price']} "
                      f"SL={desired['sl_price']} -- trading disabled")
                return
            result = broker.send_pending_order(symbol, direction, desired["entry_price"], sym_cfg.lots,
                                                desired.get("sl_price"), magic, cfg.deviation_points, comment)
            if result.ok:
                tracker.set(symbol, TrackedOrder("PENDING", result.ticket, direction, exec_timeframe, exec_start_time))
                print(f"{tag} {symbol}: placed pending order {result.ticket} "
                      f"@ {desired['entry_price']} SL={desired['sl_price']}")
            else:
                print(f"{tag} {symbol}: FAILED to place pending order -- retcode={result.retcode} {result.comment}")

    elif status == "FILLED":
        # A still-tracked PENDING order from a DIFFERENT setup than what's
        # now desired (different exec_timeframe/exec_start_time) -- the
        # source moved on (cancel-and-replace, or this exact trigger fired
        # a totally separate later setup) without that old pending order
        # ever getting cancelled here first. Added 2026-08-20 after a real
        # live incident: XAUUSD's M5 pending order (128495593) sat
        # untouched in MT5 for 20+ minutes after Trend Manager's own state
        # moved on to a fresh M1 MARKET fill -- this exact combination
        # (tracked.kind == PENDING, status == FILLED, different setup)
        # fell through every existing branch below as a silent no-op:
        # the `tracked is None` branch never ran (tracked wasn't None),
        # and the `elif tracked.kind == "PENDING"` branch only ever
        # checked for a matching position under the NEW comment, which
        # will never exist until the new market order is actually placed.
        # Mirrors the PENDING status branch's own identical
        # "superseded by a better setup" handling above.
        if tracked is not None and tracked.kind == "PENDING" and \
                (tracked.exec_timeframe, tracked.exec_start_time) != (exec_timeframe, exec_start_time):
            _cancel_or_close_tracked(cfg, source, tracker, symbol, tracked, "superseded by a better setup")
            tracked = None

        if tracked is None:
            if desired.get("mode") == "MARKET":
                if _cooldown_blocks(source, symbol, exec_timeframe, exec_start_time):
                    print(f"{tag} {symbol}: still in post-manual-close cooldown -- not re-placing yet")
                    return
                if not cfg.enable_trading:
                    print(f"{tag} {symbol}: WOULD place {direction} MARKET SL={desired['sl_price']} -- trading disabled")
                    return
                result = broker.send_market_order(symbol, direction, sym_cfg.lots, desired.get("sl_price"),
                                                   magic, cfg.deviation_points, comment)
                if result.ok:
                    tracker.set(symbol, TrackedOrder("POSITION", result.ticket, direction, exec_timeframe, exec_start_time))
                    print(f"{tag} {symbol}: placed market order, position {result.ticket}")
                else:
                    print(f"{tag} {symbol}: FAILED to place market order -- retcode={result.retcode} {result.comment}")
            else:
                # A PENDING order the source's own price-crossed simulation
                # says has been reached -- the broker should already have
                # filled the real one on its own. Look for the resulting position.
                existing_position = _find_matching_position(symbol, magic, comment)
                if existing_position is not None:
                    tracker.set(symbol, TrackedOrder("POSITION", existing_position.ticket, direction,
                                                      exec_timeframe, exec_start_time))
                    print(f"{tag} {symbol}: pending order filled, now tracking position {existing_position.ticket}")
                elif cfg.enable_trading:
                    print(f"{tag} {symbol}: source says filled but no matching real position found yet "
                          f"-- will recheck next cycle")
        elif tracked.kind == "PENDING":
            existing_position = _find_matching_position(symbol, magic, comment)
            if existing_position is not None:
                tracker.set(symbol, TrackedOrder("POSITION", existing_position.ticket, direction,
                                                  exec_timeframe, exec_start_time))
                print(f"{tag} {symbol}: pending order filled, now tracking position {existing_position.ticket}")
        # tracked.kind == "POSITION" already -- nothing more to do.


class SourceRuntime:
    """Bundles one source's own OrderTracker + SLStateStore +
    ExitStateStore -- kept together so main()/run_once() can loop over
    sources generically."""
    def __init__(self, source: SourceConfig):
        self.source = source
        self.tracker = OrderTracker(source.order_state_file)
        self.sl_states = SLStateStore(source.sl_state_file)
        self.exit_states = ExitStateStore(source.exit_state_file)


def run_once(cfg: Config, runtimes: list) -> None:
    for runtime in runtimes:
        source = runtime.source
        desired_state = _read_desired_state(source.decision_state_file)
        for sym_cfg in cfg.symbols:
            try:
                just_cleared = _check_disappeared(cfg, source, runtime.tracker, sym_cfg.symbol)
                if just_cleared:
                    # desired_state (read once, at the top of this loop)
                    # predates what _check_disappeared just found -- the
                    # source Manager hasn't had a chance to react yet.
                    # Skip reconciling this symbol for the rest of THIS
                    # cycle; the next cycle re-reads desired_state fresh,
                    # by which point it's caught up. See
                    # _check_disappeared's own docstring for the two real
                    # incidents (duplicate fills, an undone manual close)
                    # this prevents.
                    print(f"[execution_bridge:{source.name}] {sym_cfg.symbol}: skipping reconcile this cycle -- "
                          f"letting {source.name}'s own state catch up first")
                    continue
                _reconcile(cfg, source, runtime.tracker, sym_cfg, desired_state.get(sym_cfg.symbol))
            except Exception as exc:
                print(f"[execution_bridge:{source.name}] {sym_cfg.symbol} ERROR: {exc}")
        exit_manager.run_once(cfg, source, runtime.tracker, runtime.exit_states)
        stoploss_manager.run_once(cfg, source, runtime.tracker, runtime.sl_states)


def main() -> None:
    cfg = load_config()
    broker.connect(cfg)
    runtimes = [SourceRuntime(source) for source in cfg.sources]
    mode = "LIVE TRADING" if cfg.enable_trading else "dry run (EXECUTION_BRIDGE_ENABLE_TRADING=false)"
    print(f"[execution_bridge] watching {[s.symbol for s in cfg.symbols]} across sources "
          f"{[s.name for s in cfg.sources]}, polling every {cfg.poll_seconds}s -- {mode}")
    try:
        while True:
            try:
                run_once(cfg, runtimes)
            except Exception as exc:
                print(f"[execution_bridge] ERROR: {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
