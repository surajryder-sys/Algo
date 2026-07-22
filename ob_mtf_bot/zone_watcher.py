"""Stateful per-zone touch tracking.

When price first touches a zone, tracking starts for that zone specifically.
Every following candle is checked for a reversal confirmation
(reversal_signals.check_bearish_zone / check_bullish_zone / is_engulfing)
until either a signal fires or price leaves the zone without confirming. At
most one signal is emitted per touch episode - if price returns to the same
zone later, that is a new episode and can fire again.

This runs across every zone on every timeframe from the bridge concurrently
each cycle, so whichever timeframe confirms first is reported first - no
timeframe is favored or waited on over another.

Read-only - identifies signals, does not place, modify, or close any orders.

Run with: python -m ob_mtf_bot.zone_watcher
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

from ob_mtf_bot.bridge_reader import BridgeState, read_bridge_state
from ob_mtf_bot.config import load_config
from ob_mtf_bot.connection import connect, disconnect, ensure_symbol
from ob_mtf_bot.reversal_signals import (
    ReversalSignal,
    candle_dict,
    check_bearish_zone,
    check_bullish_zone,
    is_engulfing,
)

log = logging.getLogger(__name__)

STATE_PATH = Path("zone_watch_state.json")
SIGNAL_LOG_PATH = Path("zone_signals_log.txt")

IDLE = "IDLE"
TOUCHING = "TOUCHING"
FIRED = "FIRED"


def _zone_key(zone_type: str, tf: str, identity: str) -> str:
    return f"{zone_type}|{tf}|{identity}"


def _overlaps(low: float, high: float, c: dict) -> bool:
    return c["low"] <= high and c["high"] >= low


class ZoneWatcher:
    """Persists touch-episode state per zone so tracking survives restarts."""

    def __init__(self, path: Path):
        self.path = path
        self._state: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._state = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._state = {}

    def save(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2))

    def get(self, key: str) -> str:
        return self._state.get(key, IDLE)

    def set(self, key: str, value: str) -> None:
        self._state[key] = value

    def prune(self, valid_keys: set[str]) -> None:
        """Drops state for zones that no longer exist in the bridge (e.g. an
        FVG that got invalidated and was deleted upstream)."""
        for k in list(self._state.keys()):
            if k not in valid_keys:
                del self._state[k]


def process_candle(watcher: ZoneWatcher, zone_type: str, tf: str, identity: str,
                    direction: int, low: float, high: float,
                    prev_c: dict | None, c: dict) -> ReversalSignal | None:
    key = _zone_key(zone_type, tf, identity)
    state = watcher.get(key)
    touching_now = _overlaps(low, high, c)

    if state == IDLE:
        if touching_now:
            watcher.set(key, TOUCHING)
        return None

    if state == TOUCHING:
        kind = check_bearish_zone(low, high, c) if direction == -1 else check_bullish_zone(low, high, c)
        engulf = prev_c is not None and touching_now and is_engulfing(prev_c, c, direction)
        if kind or engulf:
            watcher.set(key, FIRED)
            return ReversalSignal(zone_type, tf, identity, direction, kind or "engulfing", low, high,
                                   c["time"], c["open"], c["high"], c["low"], c["close"])
        if not touching_now:
            watcher.set(key, IDLE)
        return None

    # FIRED: stays fired while still touching, re-arms once price departs.
    if not touching_now:
        watcher.set(key, IDLE)
    return None


def scan_once(watcher: ZoneWatcher, state: BridgeState, prev_c: dict | None, c: dict) -> list[ReversalSignal]:
    signals: list[ReversalSignal] = []
    valid_keys: set[str] = set()

    for z in state.order_blocks:
        direction = 1 if z.direction == "BULLISH" else -1
        valid_keys.add(_zone_key("OB", z.tf, z.signature))
        sig = process_candle(watcher, "OB", z.tf, z.signature, direction, z.low, z.high, prev_c, c)
        if sig:
            signals.append(sig)

    for z in state.fvgs:
        direction = 1 if z.direction == "BULLISH" else -1
        valid_keys.add(_zone_key("FVG", z.tf, z.name))
        sig = process_candle(watcher, "FVG", z.tf, z.name, direction, z.low, z.high, prev_c, c)
        if sig:
            signals.append(sig)

    watcher.prune(valid_keys)
    watcher.save()
    return signals


def _format_signal(s: ReversalSignal) -> str:
    t = datetime.fromtimestamp(s.candle_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    dirn = "LONG" if s.direction == 1 else "SHORT"
    return (f"{t} UTC [{s.tf}] {s.zone_type} {s.zone_low:.2f}-{s.zone_high:.2f} "
            f"{dirn} {s.signal_kind} candle O={s.candle_open:.2f} H={s.candle_high:.2f} "
            f"L={s.candle_low:.2f} C={s.candle_close:.2f}")


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    connect(cfg)
    ensure_symbol(cfg.symbol)

    watcher = ZoneWatcher(STATE_PATH)
    last_candle_time: int | None = None
    prev_c: dict | None = None

    log.info("Zone watcher started: symbol=%s", cfg.symbol)

    try:
        while True:
            rates = mt5.copy_rates_from_pos(cfg.symbol, mt5.TIMEFRAME_M1, 1, 1)
            if rates is not None and len(rates) > 0:
                c = candle_dict(rates[0])
                if c["time"] != last_candle_time:
                    try:
                        bridge_state = read_bridge_state()
                        signals = scan_once(watcher, bridge_state, prev_c, c)
                        for s in signals:
                            line = _format_signal(s)
                            print(line)
                            with SIGNAL_LOG_PATH.open("a", encoding="utf-8") as f:
                                f.write(line + "\n")
                    except FileNotFoundError as exc:
                        log.warning("Bridge file not ready yet: %s", exc)
                    prev_c = c
                    last_candle_time = c["time"]
            time.sleep(3)
    except KeyboardInterrupt:
        log.info("Stopping on user interrupt.")
    finally:
        disconnect()


if __name__ == "__main__":
    run()
