"""ICT Block Watcher -- standalone live-tracking process for the merged
TV+MT5 OB-zone store (ict_ob_block.py, M15/M5/M3). Data Manager's third
sub-component (2026-09-24), alongside tv_scraper and nlb_nsb_watcher --
same shape as nlb_nsb_watcher.py, adapted for the two-source store.

This process's only job every cycle, per symbol:
  1. Seed any newly-formed zone from either source (ICTBlockStore.sync()
     -- TV via the scraper's own store, MT5 via ob_bridge_lite.py).
  2. Read one live MT5 tick (bid/ask) plus the lowest bid / highest ask of
     every tick since the previous cycle (broker.price_extremes_since) --
     same wick-safe extremes tracking nlb_nsb_watcher.py already uses.
  3. Feed those to ICTBlockStore.update_live(), which marks any newly-
     retested zone and deletes any newly-invalidated one.

No MT5 orders are ever placed here -- pure data-tracking, same category
as tv_scraper/nlb_nsb_watcher, not a trading bot. Read-only against the
broker; only ever writes its own block/heartbeat state files.

Run with: python -m v7_sentinel.ict_ob_watcher
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import MetaTrader5 as mt5
from dotenv import load_dotenv

from v7_sentinel import broker, config, heartbeat
from v7_sentinel.ict_ob_block import ICTBlockStore

load_dotenv()

# Same TV scraper output file nlb_nsb_watcher.py reads -- read-only source, never written here.
_V7S_ZONE_STATE_FILE = os.getenv("V7S_TV_SCRAPER_ZONE_STATE_FILE", "v7s_tv_scraper_zones.json")


@dataclass(frozen=True)
class WatcherSymbolConfig:
    symbol: str
    zone_state_file: str          # the V7S scraper's store -- read-only source
    block_state_file: str          # this module's own persisted ICTBlockStore
    heartbeat_file: str


def load_symbol_configs() -> list[WatcherSymbolConfig]:
    return [
        WatcherSymbolConfig(
            symbol=symbol,
            zone_state_file=_V7S_ZONE_STATE_FILE,
            block_state_file=config.state_file_for("ict_ob_block", symbol),
            heartbeat_file=config.state_file_for("ict_ob_watcher_heartbeat", symbol),
        )
        for symbol in config.ACTIVE_SYMBOLS
    ]


_last_tick_msc: dict[str, int] = {}   # per symbol: time_msc of the newest tick already looked at


def run_once(cfg: WatcherSymbolConfig, store: ICTBlockStore) -> None:
    added, pruned = store.sync(cfg.zone_state_file, cfg.symbol)
    if added:
        print(f"[V7S-ICTBLOCK] {cfg.symbol} seeded {added} new zone(s)")
    if pruned:
        print(f"[V7S-ICTBLOCK] {cfg.symbol} pruned {pruned} zone(s) no longer on chart")

    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        print(f"[V7S-ICTBLOCK] {cfg.symbol} no live tick available -- skipping this cycle")
        return

    bid_low = ask_high = None
    since = _last_tick_msc.get(cfg.symbol)
    if since:
        lo, hi, newest = broker.price_extremes_since(cfg.symbol, since)
        if lo is not None:
            bid_low, ask_high = lo, hi
        _last_tick_msc[cfg.symbol] = max(since, newest)
    else:                                              # first cycle: look from now, never from history
        _last_tick_msc[cfg.symbol] = int(tick.time_msc)
    retested, invalidated = store.update_live(tick.bid, tick.ask, bid_low=bid_low, ask_high=ask_high)
    for zid in retested:
        print(f"[V7S-ICTBLOCK] {cfg.symbol} RETESTED (live) -- {zid}")
    for zid in invalidated:
        print(f"[V7S-ICTBLOCK] {cfg.symbol} INVALIDATED (live, deleted) -- {zid}")


def main() -> None:
    symbol_configs = load_symbol_configs()
    print(f"[V7S-ICTBLOCK] starting -- symbols={[c.symbol for c in symbol_configs]} "
          f"poll={config.POLL_SECONDS}s zone_source={_V7S_ZONE_STATE_FILE}")

    if not mt5.initialize(path=config.MT5_TERMINAL_PATH):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    stores = {c.symbol: ICTBlockStore(c.block_state_file) for c in symbol_configs}

    try:
        while True:
            for cfg in symbol_configs:
                try:
                    run_once(cfg, stores[cfg.symbol])
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V7S-ICTBLOCK] {cfg.symbol} cycle error: {exc!r}")
                heartbeat.write(cfg.heartbeat_file)
            time.sleep(config.POLL_SECONDS)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
