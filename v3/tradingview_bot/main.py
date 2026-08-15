"""Main loop: tv_bridge -> zone/ATR state stores.

Run with: python -m tradingview_bot.main

v1 scope: no trading. It resumes from the last processed byte offset in the
shared signal log tv_bridge.receiver writes, folds each event into the
matching store (ZoneStore for ob_zone_formed/ob_zone_mitigated/
ob_zone_retested, AtrStore for atr_trail), and prints it. Entry/exit logic
gets built on top of this once the strategy is defined.
"""
from __future__ import annotations

import time

from v3.tradingview_bot.atr_store import AtrStore
from v3.tradingview_bot.config import Config, load_config
from v3.tradingview_bot.state_store import SignalStore
from v3.tradingview_bot.zone_store import ZoneStore
from v3.tv_bridge.reader import read_new


def run_once(cfg: Config, cursor_store: SignalStore, zones: ZoneStore, atr: AtrStore) -> None:
    events, new_cursor = read_new(cfg.signal_log_file, cursor_store.cursor)
    if not events:
        return

    for ev in events:
        print(f"[{ev.type.upper()}] {ev.symbol} {ev.data}")
        timeframe = ev.data.get("timeframe", "")
        if ev.type == "atr_trail":
            atr.apply(ev.symbol, timeframe, ev.data, ev.received_at)
        elif ev.type == "ob_zone_formed":
            zones.apply_formed(ev.symbol, timeframe, ev.data["direction"], ev.data)
        elif ev.type == "ob_zone_mitigated":
            zones.apply_mitigated(ev.symbol, timeframe, ev.data["direction"], ev.data)
        elif ev.type == "ob_zone_retested":
            zones.apply_retested(ev.symbol, timeframe, ev.data["direction"], ev.data)

    cursor_store.record(new_cursor, [ev.data for ev in events])


def main() -> None:
    cfg = load_config()
    print(f"TradingView bot starting | log={cfg.signal_log_file} state={cfg.state_file}")
    cursor_store = SignalStore(cfg.state_file)
    zones = ZoneStore(cfg.zone_state_file)
    atr = AtrStore(cfg.atr_state_file)

    try:
        while True:
            try:
                run_once(cfg, cursor_store, zones, atr)
            except Exception as exc:
                print(f"[ERROR] {exc}")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
