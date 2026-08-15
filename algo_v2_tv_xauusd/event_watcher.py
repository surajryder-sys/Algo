"""Standalone event-history logger -- observes TradingView-sourced zone/ATR
data and logs every new fact (zone formed/retested/mitigated, ATR flip,
per-timeframe bias flip) via EventTracker/EventLog, with NO MT5 dependency
at all.

Run with: python -m algo_v2_tv_xauusd.event_watcher

Deliberately split out from algo_v2_tv_xauusd.main: that module needs MT5
(broker.connect(), get_tick_price(), position/order management) for the
actual trading logic it will grow later, but event-history logging never
did -- it only ever reads reader.py's merged TradingView data. Splitting
means event history keeps recording reliably even while MT5 connectivity
issues are being sorted out separately (confirmed live: a fresh MT5 Python
API connection on this terminal hung indefinitely after its first
successful call, with 3 other bots already connected -- same failure
class as the earlier one-off diagnostic script hangs).
"""
from __future__ import annotations

import time

from algo_v2_tv_xauusd import reader
from algo_v2_tv_xauusd.active_events import ActiveEventStore
from algo_v2_tv_xauusd.config import Config, load_config
from algo_v2_tv_xauusd.event_log import EventLog
from algo_v2_tv_xauusd.event_tracker import EventTracker
from algo_v2_tv_xauusd.main import TRACKED_TIMEFRAMES


def run_once(cfg: Config, events: EventTracker) -> None:
    for tf in TRACKED_TIMEFRAMES:
        snap = reader.read_zone(cfg.symbol, int(tf))
        atr = reader.read_atr(cfg.symbol, int(tf))
        if snap is not None:
            events.observe_zones(tf, "bull", snap.bull)
            events.observe_zones(tf, "bear", snap.bear)
        events.observe_atr(tf, atr)
        events.observe_bias(tf, atr, snap)


def main() -> None:
    cfg = load_config()
    print(f"TVX event watcher starting | symbol={cfg.symbol} log={cfg.event_log_file} "
          f"active={cfg.active_events_file} (no MT5 connection -- observation only)")

    reader.configure(cfg.tv_zone_state_file, cfg.tv_atr_state_file,
                     cfg.tv_scraper_zone_state_file, cfg.tv_scraper_atr_state_file)
    events = EventTracker(EventLog(cfg.event_log_file), cfg.symbol,
                          active=ActiveEventStore(cfg.active_events_file))

    try:
        while True:
            try:
                run_once(cfg, events)
            except Exception as exc:
                print(f"[ERROR] {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
