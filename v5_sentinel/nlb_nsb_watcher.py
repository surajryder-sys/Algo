"""NLB/NSB Block -- standalone live-tracking process. Confirmed with the
user 2026-09-09 (see nlb_nsb_block.py's own docstring for the full data-
layer design this drives). This process's only job every cycle:

  1. Pick up any newly-formed OB zone from the scraper's own store
     (BlockStore.sync_from_scraper() -- a cheap JSON read, never
     overwrites a zone this block has already seeded).
  2. Read one live MT5 tick (bid/ask) -- no bar/candle involved at all,
     this is deliberately NOT bar-close-gated (unlike M3/M5's own
     confirmation logic elsewhere in this project) -- retest and
     mitigation both need to react to a single tick, per the user's own
     "we will not wait for candle close" direction.
  3. Feed that tick to BlockStore.update_live(), which marks any newly-
     retested zone and deletes any newly-invalidated one.

No MT5 orders are ever placed here -- this is a pure data-tracking
process, same category as tv_scraper.scraper or watchdog.py, not a
trading bot. Read-only against the broker; only ever writes its own
block/heartbeat state files.

Run with: python -m v5_sentinel.nlb_nsb_watcher
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import MetaTrader5 as mt5
from dotenv import load_dotenv

from v5_sentinel import heartbeat
from v5_sentinel.nlb_nsb_block import BlockStore

load_dotenv()


@dataclass(frozen=True)
class WatcherConfig:
    symbol: str
    zone_state_file: str          # tv_scraper's own store -- read-only source
    block_state_file: str          # this module's own persisted BlockStore
    heartbeat_file: str
    poll_seconds: float
    mt5_terminal_path: str | None


def load_config() -> WatcherConfig:
    return WatcherConfig(
        symbol=os.getenv("V5S_SYMBOL", "XAUUSD"),
        zone_state_file=os.getenv("V5S_TV_SCRAPER_ZONE_STATE_FILE", "v5s_tv_scraper_zones.json"),
        block_state_file=os.getenv("V5S_NLB_NSB_BLOCK_STATE_FILE", "v5s_nlb_nsb_block.json"),
        heartbeat_file=os.getenv("V5S_NLB_NSB_HEARTBEAT_FILE", "v5s_nlb_nsb_watcher_heartbeat.json"),
        poll_seconds=float(os.getenv("V5S_NLB_NSB_POLL_SECONDS", "1")),
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
    )


def run_once(cfg: WatcherConfig, store: BlockStore) -> None:
    added = store.sync_from_scraper(cfg.zone_state_file, cfg.symbol)
    if added:
        print(f"[V5S-NLBNSB] seeded {added} new zone(s) from scraper")

    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        print("[V5S-NLBNSB] no live tick available -- skipping this cycle")
        return

    retested, invalidated = store.update_live(tick.bid, tick.ask)
    for zid in retested:
        print(f"[V5S-NLBNSB] RETESTED (live) -- {zid}")
    for zid in invalidated:
        print(f"[V5S-NLBNSB] INVALIDATED (live, deleted) -- {zid}")


def main() -> None:
    cfg = load_config()
    print(f"[V5S-NLBNSB] starting -- symbol={cfg.symbol} poll={cfg.poll_seconds}s "
          f"zone_source={cfg.zone_state_file} block_state={cfg.block_state_file}")

    if not mt5.initialize(path=cfg.mt5_terminal_path):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    store = BlockStore(cfg.block_state_file)

    try:
        while True:
            try:
                run_once(cfg, store)
            except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                print(f"[V5S-NLBNSB] cycle error: {exc!r}")
            heartbeat.write(cfg.heartbeat_file)
            time.sleep(cfg.poll_seconds)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
