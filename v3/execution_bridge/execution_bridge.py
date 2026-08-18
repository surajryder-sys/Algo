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
   tracking. (Only Trend Manager currently reads a manual-events file;
   Reversal Manager doesn't have that feedback loop yet.)
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

Run with: python -m v3.execution_bridge.execution_bridge
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from v3.execution_bridge import broker, intervention, manual_events, stoploss_manager
from v3.execution_bridge.config import Config, SourceConfig, SymbolConfig, load_config
from v3.execution_bridge.order_tracker import OrderTracker, TrackedOrder, make_comment
from v3.execution_bridge.sl_state import SLStateStore


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


def _check_disappeared(cfg: Config, source: SourceConfig, tracker: OrderTracker, symbol: str) -> None:
    """If Execution Bridge is tracking a ticket that's no longer open in
    MT5, works out why and clears the stale tracking. Relays a manual
    event to Trend Manager's own consumer if this source is "trend"
    (Reversal Manager doesn't have that feedback loop yet). Mutates
    tracker; does not place anything new (that's _reconcile's job)."""
    tag = f"[execution_bridge:{source.name}]"
    tracked = tracker.get(symbol)
    if tracked is None:
        return

    if tracked.kind == "PENDING":
        still_there = any(o.ticket == tracked.ticket for o in broker.get_pending_orders(symbol, source.magic_number))
        if still_there:
            return
        outcome = intervention.check_pending_disappeared(tracked.ticket, tracker)
        if outcome == "manual":
            if source.manual_events_file:
                manual_events.write_event(source.manual_events_file, symbol)
                print(f"{tag} {symbol}: pending order {tracked.ticket} manually cancelled -- notified {source.name}")
            else:
                print(f"{tag} {symbol}: pending order {tracked.ticket} manually cancelled -- "
                      f"{source.name} has no relay configured, WILL NOT be notified")
        elif outcome == "filled":
            print(f"{tag} {symbol}: pending order {tracked.ticket} filled")
        tracker.clear(symbol)  # stale either way -- a fill gets re-discovered as a position below
    else:
        still_there = any(p.ticket == tracked.ticket for p in broker.get_positions(symbol, source.magic_number))
        if still_there:
            return
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
            if source.manual_events_file:
                manual_events.write_event(source.manual_events_file, symbol)
                print(f"{tag} {symbol}: position {tracked.ticket} closed ({outcome}) -- notified {source.name}")
            else:
                # Confirmed live 2026-08-18: this used to say "notified"
                # unconditionally even when there was no file to write
                # to -- misleading. A source with no manual_events_file
                # has NO way to learn about this close at all.
                print(f"{tag} {symbol}: position {tracked.ticket} closed ({outcome}) -- "
                      f"{source.name} has no relay configured, WILL NOT be notified")
        tracker.clear(symbol)


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
    comment = make_comment(source.comment_prefix, exec_timeframe, direction, exec_start_time) if exec_timeframe else None

    if status == "PENDING":
        if tracked is not None and (tracked.exec_timeframe, tracked.exec_start_time) != (exec_timeframe, exec_start_time):
            _cancel_or_close_tracked(cfg, source, tracker, symbol, tracked, "superseded by a better setup")
            tracked = None

        if tracked is None:
            existing = _find_matching_pending(symbol, magic, comment)
            if existing is not None:
                tracker.set(symbol, TrackedOrder("PENDING", existing.ticket, direction, exec_timeframe, exec_start_time))
                print(f"{tag} {symbol}: reconciled existing pending order {existing.ticket}")
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
        if tracked is None:
            if desired.get("mode") == "MARKET":
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
    """Bundles one source's own OrderTracker + SLStateStore -- kept
    together so main()/run_once() can loop over sources generically."""
    def __init__(self, source: SourceConfig):
        self.source = source
        self.tracker = OrderTracker(source.order_state_file)
        self.sl_states = SLStateStore(source.sl_state_file)


def run_once(cfg: Config, runtimes: list) -> None:
    for runtime in runtimes:
        source = runtime.source
        desired_state = _read_desired_state(source.decision_state_file)
        for sym_cfg in cfg.symbols:
            try:
                _check_disappeared(cfg, source, runtime.tracker, sym_cfg.symbol)
                _reconcile(cfg, source, runtime.tracker, sym_cfg, desired_state.get(sym_cfg.symbol))
            except Exception as exc:
                print(f"[execution_bridge:{source.name}] {sym_cfg.symbol} ERROR: {exc}")
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
