"""Execution Bridge main loop -- see v3/execution_bridge/__init__.py for
what this is. Reconciles real MT5 pending orders/positions against
whatever v3/signal_engine/trend_manager.py has already decided
(trend_manager_trade_state.json's own "active_trades", read-only here --
this module never writes to Trend Manager's state file). Never decides
direction/entry/SL itself.

Each cycle, per symbol:
1. If Execution Bridge is already tracking a real ticket, check whether
   it's disappeared from MT5 since last poll -- if so, work out why
   (filled, manually cancelled/closed, SL/TP hit, or Execution Bridge's
   own expected cancellation) via intervention.py, relay a manual event
   to Trend Manager if that's what happened (manual_events.py), then
   drop our own stale tracking of it.
2. Compare Trend Manager's desired state against what's now tracked:
   - Nothing desired (no active trade, or still AWAITING_TRIGGER) but
     something's tracked -> cancel/close it (Trend Manager's own
     decision already closed this on the signal side; this just mirrors
     it onto the real order/position).
   - PENDING desired, tracked doesn't match (Trend Manager replaced the
     proposal) -> cancel the old real order, place the new one.
   - PENDING desired, nothing tracked -> place it.
   - FILLED/MARKET desired, nothing tracked -> place a market order now.
   - FILLED/PENDING desired (a pending that Trend Manager's own
     simulation says has been reached) -> look for the real position the
     broker should have already filled it into on its own; start
     tracking it once found.

Run with: python -m v3.execution_bridge.execution_bridge
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from v3.execution_bridge import broker, intervention, manual_events
from v3.execution_bridge.config import Config, SymbolConfig, load_config
from v3.execution_bridge.order_tracker import OrderTracker, TrackedOrder, make_comment


def _read_trend_state(path: str) -> dict:
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


def _cancel_or_close_tracked(cfg: Config, tracker: OrderTracker, symbol: str,
                              tracked: TrackedOrder, reason: str) -> None:
    if not cfg.enable_trading:
        print(f"[execution_bridge] {symbol}: WOULD cancel/close ticket={tracked.ticket} "
              f"({reason}) -- trading disabled")
        tracker.clear(symbol)
        return

    if tracked.kind == "PENDING":
        tracker.mark_expected_cancellation(tracked.ticket)  # before cancelling -- see order_tracker.py
        result = broker.cancel_pending_order(tracked.ticket)
        print(f"[execution_bridge] {symbol}: cancelled pending {tracked.ticket} ({reason}) -- {result}")
    else:
        positions = [p for p in broker.get_positions(symbol, cfg.magic_number) if p.ticket == tracked.ticket]
        if positions:
            result = broker.close_position(symbol, positions[0], cfg.deviation_points, f"TM close ({reason})")
            print(f"[execution_bridge] {symbol}: closed position {tracked.ticket} ({reason}) -- {result}")
    tracker.clear(symbol)


def _check_disappeared(cfg: Config, tracker: OrderTracker, symbol: str) -> None:
    """If Execution Bridge is tracking a ticket that's no longer open in
    MT5, works out why and clears the stale tracking. May relay a
    manual event to Trend Manager. Mutates tracker; does not place
    anything new (that's _reconcile's job, called after this)."""
    tracked = tracker.get(symbol)
    if tracked is None:
        return

    if tracked.kind == "PENDING":
        still_there = any(o.ticket == tracked.ticket for o in broker.get_pending_orders(symbol, cfg.magic_number))
        if still_there:
            return
        outcome = intervention.check_pending_disappeared(tracked.ticket, tracker)
        if outcome == "manual":
            manual_events.write_event(cfg.manual_events_file, symbol)
            print(f"[execution_bridge] {symbol}: pending order {tracked.ticket} manually cancelled "
                  f"-- notified Trend Manager")
        elif outcome == "filled":
            print(f"[execution_bridge] {symbol}: pending order {tracked.ticket} filled")
        tracker.clear(symbol)  # stale either way -- a fill gets re-discovered as a position below
    else:
        still_there = any(p.ticket == tracked.ticket for p in broker.get_positions(symbol, cfg.magic_number))
        if still_there:
            return
        outcome = intervention.check_position_disappeared(tracked.ticket)
        if outcome == "manual":
            manual_events.write_event(cfg.manual_events_file, symbol)
            print(f"[execution_bridge] {symbol}: position {tracked.ticket} manually closed "
                  f"-- notified Trend Manager")
        elif outcome in ("sl", "tp"):
            print(f"[execution_bridge] {symbol}: position {tracked.ticket} closed via {outcome.upper()} hit")
        tracker.clear(symbol)


def _reconcile(cfg: Config, tracker: OrderTracker, sym_cfg: SymbolConfig, desired: Optional[dict]) -> None:
    symbol = sym_cfg.symbol
    magic = cfg.magic_number
    tracked = tracker.get(symbol)

    if desired is None or desired.get("status") == "AWAITING_TRIGGER":
        if tracked is not None:
            _cancel_or_close_tracked(cfg, tracker, symbol, tracked, "no longer desired by Trend Manager")
        return

    direction = desired["direction"]
    status = desired["status"]
    exec_timeframe = desired.get("exec_timeframe")
    exec_start_time = desired.get("exec_start_time")
    comment = make_comment(exec_timeframe, direction, exec_start_time) if exec_timeframe else None

    if status == "PENDING":
        if tracked is not None and (tracked.exec_timeframe, tracked.exec_start_time) != (exec_timeframe, exec_start_time):
            _cancel_or_close_tracked(cfg, tracker, symbol, tracked, "superseded by a better setup")
            tracked = None

        if tracked is None:
            existing = _find_matching_pending(symbol, magic, comment)
            if existing is not None:
                tracker.set(symbol, TrackedOrder("PENDING", existing.ticket, direction, exec_timeframe, exec_start_time))
                print(f"[execution_bridge] {symbol}: reconciled existing pending order {existing.ticket}")
                return
            if not cfg.enable_trading:
                print(f"[execution_bridge] {symbol}: WOULD place {direction} LIMIT @ {desired['entry_price']} "
                      f"SL={desired['sl_price']} -- trading disabled")
                return
            result = broker.send_pending_order(symbol, direction, desired["entry_price"], sym_cfg.lots,
                                                desired.get("sl_price"), magic, cfg.deviation_points, comment)
            if result.ok:
                tracker.set(symbol, TrackedOrder("PENDING", result.ticket, direction, exec_timeframe, exec_start_time))
                print(f"[execution_bridge] {symbol}: placed pending order {result.ticket} "
                      f"@ {desired['entry_price']} SL={desired['sl_price']}")
            else:
                print(f"[execution_bridge] {symbol}: FAILED to place pending order -- "
                      f"retcode={result.retcode} {result.comment}")

    elif status == "FILLED":
        if tracked is None:
            if desired.get("mode") == "MARKET":
                if not cfg.enable_trading:
                    print(f"[execution_bridge] {symbol}: WOULD place {direction} MARKET "
                          f"SL={desired['sl_price']} -- trading disabled")
                    return
                result = broker.send_market_order(symbol, direction, sym_cfg.lots, desired.get("sl_price"),
                                                   magic, cfg.deviation_points, comment)
                if result.ok:
                    tracker.set(symbol, TrackedOrder("POSITION", result.ticket, direction, exec_timeframe, exec_start_time))
                    print(f"[execution_bridge] {symbol}: placed market order, position {result.ticket}")
                else:
                    print(f"[execution_bridge] {symbol}: FAILED to place market order -- "
                          f"retcode={result.retcode} {result.comment}")
            else:
                # A PENDING order Trend Manager's own price-crossed simulation
                # says has been reached -- the broker should already have
                # filled the real one on its own. Look for the resulting position.
                existing_position = _find_matching_position(symbol, magic, comment)
                if existing_position is not None:
                    tracker.set(symbol, TrackedOrder("POSITION", existing_position.ticket, direction,
                                                      exec_timeframe, exec_start_time))
                    print(f"[execution_bridge] {symbol}: pending order filled, now tracking position "
                          f"{existing_position.ticket}")
                elif cfg.enable_trading:
                    print(f"[execution_bridge] {symbol}: Trend Manager says filled but no matching real "
                          f"position found yet -- will recheck next cycle")
        elif tracked.kind == "PENDING":
            existing_position = _find_matching_position(symbol, magic, comment)
            if existing_position is not None:
                tracker.set(symbol, TrackedOrder("POSITION", existing_position.ticket, direction,
                                                  exec_timeframe, exec_start_time))
                print(f"[execution_bridge] {symbol}: pending order filled, now tracking position "
                      f"{existing_position.ticket}")
        # tracked.kind == "POSITION" already -- nothing more to do.


def run_once(cfg: Config, tracker: OrderTracker) -> None:
    trend_state = _read_trend_state(cfg.trend_state_file)
    for sym_cfg in cfg.symbols:
        try:
            _check_disappeared(cfg, tracker, sym_cfg.symbol)
            _reconcile(cfg, tracker, sym_cfg, trend_state.get(sym_cfg.symbol))
        except Exception as exc:
            print(f"[execution_bridge] {sym_cfg.symbol} ERROR: {exc}")


def main() -> None:
    cfg = load_config()
    broker.connect(cfg)
    tracker = OrderTracker(cfg.order_state_file)
    mode = "LIVE TRADING" if cfg.enable_trading else "dry run (EXECUTION_BRIDGE_ENABLE_TRADING=false)"
    print(f"[execution_bridge] watching {[s.symbol for s in cfg.symbols]}, polling every "
          f"{cfg.poll_seconds}s -- {mode}")
    try:
        while True:
            try:
                run_once(cfg, tracker)
            except Exception as exc:
                print(f"[execution_bridge] ERROR: {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
