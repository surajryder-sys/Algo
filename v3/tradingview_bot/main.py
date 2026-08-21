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
from pathlib import Path

from v3.tradingview_bot.atr_store import AtrStore
from v3.tradingview_bot.config import Config, load_config
from v3.tradingview_bot.state_store import SignalStore
from v3.tradingview_bot.zone_store import ZoneStore
from v3.tv_bridge.reader import read_new


def _trim_if_caught_up(log_file: str, cursor_store: SignalStore, cursor: int) -> None:
    """Once every appended line has been folded into ZoneStore/AtrStore, the
    raw log is just a fully-replayed buffer with nothing left worth keeping
    -- ZoneStore already drops a zone the moment it's mitigated (see its own
    apply_mitigated docstring), so THIS is the only remaining place raw
    events piled up forever. Truncate it and reset the cursor to 0 together
    so the next read starts clean at byte 0 of an empty file, never at a
    cursor pointing past a file that's since shrunk (see the incident this
    is fixing: a manual edit to this file once left the cursor pointing past
    EOF, and every subsequent poll silently read zero events until caught).

    Known race, accepted rather than worked around: tv_bridge.receiver runs
    in a SEPARATE process and reopens the file fresh per write (see its own
    _append_signal). If a new event lands in the split second between this
    process's read reaching EOF and the truncate() below, that one event
    can be silently missed instead of picked up next poll. Rare -- needs a
    receiver write to land inside a single truncate() call's window -- and
    self-healing in practice: a missed ob_zone_formed just means a later
    ob_zone_mitigated for it finds no zone to remove and no-ops instead of
    erroring. Not worth a cross-process lock for that.
    """
    path = Path(log_file)
    if cursor == 0 or not path.exists() or path.stat().st_size != cursor:
        return
    with path.open("r+", encoding="utf-8") as f:
        f.truncate(0)
    cursor_store.record(0)


def run_once(cfg: Config, cursor_store: SignalStore, zones: ZoneStore, atr: AtrStore) -> None:
    events, new_cursor = read_new(cfg.signal_log_file, cursor_store.cursor)
    if not events:
        _trim_if_caught_up(cfg.signal_log_file, cursor_store, new_cursor)
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

    cursor_store.record(new_cursor)
    _trim_if_caught_up(cfg.signal_log_file, cursor_store, new_cursor)


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
