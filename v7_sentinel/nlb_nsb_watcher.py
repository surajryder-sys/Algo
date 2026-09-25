"""NLB/NSB Block -- standalone live-tracking process. Ported from
v5_sentinel/nlb_nsb_watcher.py (2026-09-18), reshaped for V7S's
multi-instrument design (one BlockStore + one zone_state_file per
symbol, looped each poll -- see config.ACTIVE_SYMBOLS) and for the
zone data source: `zone_state_file` below points at the V7S scraper's
output file (v7_sentinel/tv_scraper, "V7S Scraper" -- renamed/ported from
V5S's scraper 2026-09-21), READ-ONLY. V7S's watcher needs that scraper
process to be running. This module NEVER writes to that file.

This process's only job every cycle, per symbol:
  1. Pick up any newly-formed OB zone from the scraper's own store
     (BlockStore.sync_from_scraper() -- a cheap JSON read, never
     overwrites a zone this block has already seeded).
  2. Read one live MT5 tick (bid/ask) PLUS the lowest bid / highest ask of
     every tick since the previous cycle (broker.price_extremes_since) --
     no bar/candle involved at all, this is deliberately NOT bar-close-gated.
     The extremes mean a wick shorter than the poll interval is still seen
     (2026-09-21: a 0.11 s wick through an H1 support was missed by plain
     once-a-second sampling).
  3. Feed those to BlockStore.update_live(), which marks any newly-
     retested zone and deletes any newly-invalidated one.

No MT5 orders are ever placed here -- this is a pure data-tracking
process, same category as tv_scraper.scraper or a watchdog, not a
trading bot. Read-only against the broker; only ever writes its own
block/heartbeat state files (never the scraper's zone file).

Run with: python -m v7_sentinel.nlb_nsb_watcher
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import MetaTrader5 as mt5
from dotenv import load_dotenv

from v7_sentinel import broker, config, heartbeat
from v7_sentinel.nlb_nsb_block import BlockStore

load_dotenv()

# The V7S Scraper's output file (v7_sentinel/tv_scraper) -- read-only source,
# never written here. (2026-09-18 this read V5S's scraper output instead;
# renamed to V7S's own scraper 2026-09-21 so V5S can be retired.)
_V7S_ZONE_STATE_FILE = os.getenv("V7S_TV_SCRAPER_ZONE_STATE_FILE", "v7s_tv_scraper_zones.json")


@dataclass(frozen=True)
class WatcherSymbolConfig:
    symbol: str
    zone_state_file: str          # the V7S scraper's store -- read-only source
    block_state_file: str          # this module's own persisted BlockStore
    heartbeat_file: str


def load_symbol_configs() -> list[WatcherSymbolConfig]:
    return [
        WatcherSymbolConfig(
            symbol=symbol,
            zone_state_file=_V7S_ZONE_STATE_FILE,
            block_state_file=config.state_file_for("nlb_nsb_block", symbol),
            heartbeat_file=config.state_file_for("nlb_nsb_watcher_heartbeat", symbol),
        )
        for symbol in config.ACTIVE_SYMBOLS
    ]


_last_tick_msc: dict[str, int] = {}   # per symbol: time_msc of the newest tick already looked at


def run_once(cfg: WatcherSymbolConfig, store: BlockStore) -> None:
    # sync_from_scraper() both adds new zones AND prunes any the scraper's own current
    # top-4-per-side view no longer includes (pruning-by-absence reinstated 2026-09-25,
    # see BlockStore.sync_from_scraper's own docstring for the full "why" and its known
    # tradeoff). A zone also still leaves the Block whenever update_live() below finds it
    # genuinely invalidated by real price -- two independent ways out now, not one.
    added, pruned = store.sync_from_scraper(cfg.zone_state_file, cfg.symbol)
    if added:
        print(f"[V7S-NLBNSB] {cfg.symbol} seeded {added} new zone(s) from scraper")
    if pruned:
        print(f"[V7S-NLBNSB] {cfg.symbol} pruned {pruned} zone(s) no longer on chart")

    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick is None:
        print(f"[V7S-NLBNSB] {cfg.symbol} no live tick available -- skipping this cycle")
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
        print(f"[V7S-NLBNSB] {cfg.symbol} RETESTED (live) -- {zid}")
    for zid in invalidated:
        print(f"[V7S-NLBNSB] {cfg.symbol} INVALIDATED (live, deleted) -- {zid}")


def main() -> None:
    symbol_configs = load_symbol_configs()
    print(f"[V7S-NLBNSB] starting -- symbols={[c.symbol for c in symbol_configs]} "
          f"poll={config.POLL_SECONDS}s zone_source={_V7S_ZONE_STATE_FILE}")

    if not mt5.initialize(path=config.MT5_TERMINAL_PATH):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    stores = {c.symbol: BlockStore(c.block_state_file) for c in symbol_configs}

    try:
        while True:
            for cfg in symbol_configs:
                try:
                    run_once(cfg, stores[cfg.symbol])
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V7S-NLBNSB] {cfg.symbol} cycle error: {exc!r}")
                heartbeat.write(cfg.heartbeat_file)
            time.sleep(config.POLL_SECONDS)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
