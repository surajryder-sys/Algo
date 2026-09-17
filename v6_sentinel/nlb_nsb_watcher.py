"""NLB/NSB Block -- standalone live-tracking process. Ported from
v5_sentinel/nlb_nsb_watcher.py (2026-09-18), reshaped for V6S's
multi-instrument design (one BlockStore + one zone_state_file per
symbol, looped each poll -- see config.ACTIVE_SYMBOLS) and for the
confirmed zone-data-source decision: `zone_state_file` below points at
V5S's OWN tv_scraper output file, READ-ONLY (see
project_v6_sentinel_architecture memory for the full reasoning -- avoids
a second scraper/browser window; V6S accepts a runtime dependency on
V5S's tv_scraper process staying alive as the trade-off). This module
NEVER writes to that file.

This process's only job every cycle, per symbol:
  1. Pick up any newly-formed OB zone from the scraper's own store
     (BlockStore.sync_from_scraper() -- a cheap JSON read, never
     overwrites a zone this block has already seeded).
  2. Read one live MT5 tick (bid/ask) -- no bar/candle involved at all,
     this is deliberately NOT bar-close-gated -- retest and mitigation
     both need to react to a single tick.
  3. Feed that tick to BlockStore.update_live(), which marks any newly-
     retested zone and deletes any newly-invalidated one.

No MT5 orders are ever placed here -- this is a pure data-tracking
process, same category as tv_scraper.scraper or a watchdog, not a
trading bot. Read-only against the broker; only ever writes its own
block/heartbeat state files (never V5S's zone file).

Run with: python -m v6_sentinel.nlb_nsb_watcher
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import MetaTrader5 as mt5
from dotenv import load_dotenv

from v6_sentinel import config, heartbeat
from v6_sentinel.nlb_nsb_block import BlockStore

load_dotenv()

# V5-Sentinel's OWN tv_scraper output file -- read-only source, never
# written here. Confirmed with the user 2026-09-18: reuse V5S's already-
# running scraper rather than run a second browser window for V6S.
_V5S_ZONE_STATE_FILE = os.getenv("V5S_TV_SCRAPER_ZONE_STATE_FILE", "v5s_tv_scraper_zones.json")


@dataclass(frozen=True)
class WatcherSymbolConfig:
    symbol: str
    zone_state_file: str          # V5S's tv_scraper store -- read-only source
    block_state_file: str          # this module's own persisted BlockStore
    heartbeat_file: str


def load_symbol_configs() -> list[WatcherSymbolConfig]:
    return [
        WatcherSymbolConfig(
            symbol=symbol,
            zone_state_file=_V5S_ZONE_STATE_FILE,
            block_state_file=config.state_file_for("nlb_nsb_block", symbol),
            heartbeat_file=config.state_file_for("nlb_nsb_watcher_heartbeat", symbol),
        )
        for symbol in config.ACTIVE_SYMBOLS
    ]


def run_once(cfg: WatcherSymbolConfig, store: BlockStore) -> None:
    added, pruned = store.sync_from_scraper(cfg.zone_state_file, cfg.symbol)
    if added:
        print(f"[V6S-NLBNSB] {cfg.symbol} seeded {added} new zone(s) from scraper")
    if pruned:
        print(f"[V6S-NLBNSB] {cfg.symbol} pruned {pruned} zone(s) no longer reported by scraper")

    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        print(f"[V6S-NLBNSB] {cfg.symbol} no live tick available -- skipping this cycle")
        return

    retested, invalidated = store.update_live(tick.bid, tick.ask)
    for zid in retested:
        print(f"[V6S-NLBNSB] {cfg.symbol} RETESTED (live) -- {zid}")
    for zid in invalidated:
        print(f"[V6S-NLBNSB] {cfg.symbol} INVALIDATED (live, deleted) -- {zid}")


def main() -> None:
    symbol_configs = load_symbol_configs()
    print(f"[V6S-NLBNSB] starting -- symbols={[c.symbol for c in symbol_configs]} "
          f"poll={config.POLL_SECONDS}s zone_source={_V5S_ZONE_STATE_FILE}")

    if not mt5.initialize(path=config.MT5_TERMINAL_PATH):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    stores = {c.symbol: BlockStore(c.block_state_file) for c in symbol_configs}

    try:
        while True:
            for cfg in symbol_configs:
                try:
                    run_once(cfg, stores[cfg.symbol])
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V6S-NLBNSB] {cfg.symbol} cycle error: {exc!r}")
                heartbeat.write(cfg.heartbeat_file)
            time.sleep(config.POLL_SECONDS)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
