"""Live execution loop for the zone-reaction reversal strategy.

Entry: any confirmed reversal signal (rejection_close / wick_rejection /
engulfing) at any OB or FVG zone, on any timeframe, independently. SL at the
zone edge with the existing risk.py minimum-distance floor. No TP is ever
set - positions are managed purely by the trailing stop and the
signal-based auto-exit below, whichever fires first.

Reversal: an opposing signal while already in a trade closes and flips,
using that same signal as the new entry - UNLESS the zone is capped or
paused (see reversal_cap.py), in which case a strong signal (wick_rejection
/engulfing) only closes (no flip) and a plain rejection_close is ignored.

New entries are skipped entirely from a paused (two-zone ping-pong) zone.
A capped (single-zone) zone still takes new entries identically to a normal
zone right now - the original "tight scalp target" plan for capped zones
no longer applies now that no TP is set anywhere; flagged as an open point.

Trailing: once in profit, trails to the second-nearest same-direction zone
(unified OB+FVG ranking, skipping the nearest), ratchets forward only.

Position sizing is fixed at cfg.lots (0.01) - no risk-based sizing yet, by
design, pending backtest/live-test results.

Gated by cfg.enable_trading (OB_ENABLE_TRADING in .env) - set it to false
to dry-run (log intended actions without placing real orders) before going
live. Trailing is separately gated by cfg.enable_trailing.

Run with: python -m ob_mtf_bot.reversal_trader
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

from ob_mtf_bot.bridge_reader import read_bridge_state
from ob_mtf_bot.config import Config, load_config
from ob_mtf_bot.connection import connect, disconnect, ensure_symbol
from ob_mtf_bot.execution import close_position, managed_position
from ob_mtf_bot.reversal_cap import ReversalCapTracker
from ob_mtf_bot.reversal_signals import ReversalSignal, candle_dict
from ob_mtf_bot.risk import apply_minimum_sl, sl_geometry_valid
from ob_mtf_bot.state_store import StateStore
from ob_mtf_bot.zone_targets import (
    all_zone_refs,
    second_nearest_zone_for_trailing,
    zone_broken,
    zone_key,
)
from ob_mtf_bot.zone_watcher import ZoneWatcher, scan_once

log = logging.getLogger(__name__)

# Distinct from cfg.magic_number so this experimental engine's positions are
# never confused with the older ob_mtf_bot engine's, if both are ever run.
MAGIC_OFFSET = 1000

ZONE_STATE_PATH = Path("reversal_trader_zone_state.json")
CAP_STATE_PATH = Path("reversal_trader_cap_state.json")
EXPOSURE_STATE_PATH = Path("reversal_trader_exposure_state.json")
LOG_PATH = Path("reversal_trader_log.txt")

STRONG_KINDS = {"wick_rejection", "engulfing"}


def _log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC {msg}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    log.info(msg)


def _magic(cfg: Config) -> int:
    return cfg.magic_number + MAGIC_OFFSET


def _digits(symbol: str) -> int:
    return mt5.symbol_info(symbol).digits


def _entry_price(symbol: str, direction: int) -> float:
    tick = mt5.symbol_info_tick(symbol)
    return tick.ask if direction == 1 else tick.bid


def _logical_sl(sig: ReversalSignal, cfg: Config) -> float:
    return (sig.zone_low - cfg.sl_buffer) if sig.direction == 1 else (sig.zone_high + cfg.sl_buffer)


def place_market_order(cfg: Config, symbol: str, direction: int, logical_sl: float, comment: str) -> bool:
    """No TP is ever set - positions are managed entirely by the trailing
    stop (update_trailing_stop) and the signal-based auto-exit in
    handle_signal, whichever fires first."""
    entry = _entry_price(symbol, direction)
    digits = _digits(symbol)
    sl = apply_minimum_sl(cfg, direction, entry, logical_sl, digits)
    if not sl_geometry_valid(symbol, direction, entry, sl):
        _log(f"REJECTED order: invalid SL geometry entry={entry:.2f} sl={sl:.2f}")
        return False

    order_type = mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": cfg.lots,
        "type": order_type,
        "price": entry,
        "sl": sl,
        "tp": 0.0,
        "deviation": cfg.deviation_points,
        "magic": _magic(cfg),
        "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        _log(f"ORDER FAILED: {result} | entry={entry:.2f} sl={sl:.2f}")
        return False

    _log(f"ENTERED {'LONG' if direction == 1 else 'SHORT'} @ {entry:.2f} sl={sl:.2f} (no TP - trailing/signal exit only) ({comment})")
    return True


def enter_from_signal(cfg: Config, symbol: str, sig: ReversalSignal) -> bool:
    logical_sl = _logical_sl(sig, cfg)
    comment = f"RTpy|{sig.zone_type}|{sig.tf}|{sig.signal_kind}"
    return place_market_order(cfg, symbol, sig.direction, logical_sl, comment)


def handle_signal(cfg: Config, symbol: str, sig: ReversalSignal,
                   cap: ReversalCapTracker, exposure: StateStore) -> None:
    zk = zone_key_from_signal(sig)
    restricted = cap.is_capped(zk) or cap.is_paused(zk)
    paused = cap.is_paused(zk)

    pos = managed_position(symbol, _magic(cfg))
    in_position = pos is not None
    current_dir = (1 if pos.type == mt5.ORDER_TYPE_BUY else -1) if in_position else 0

    if in_position and current_dir == sig.direction:
        return  # same-direction signal, already positioned, no action

    if in_position and current_dir == -sig.direction:
        if restricted:
            if sig.signal_kind in STRONG_KINDS:
                close_position(symbol, _magic(cfg), cfg.deviation_points)
                exposure.set_exposure(None)
                _log(f"CLOSED (strong signal at {'paused' if paused else 'capped'} zone, no flip) "
                     f"{sig.zone_type} {sig.tf} {sig.zone_low:.2f}-{sig.zone_high:.2f} {sig.signal_kind}")
            # weak signal at a restricted zone: ignored, hold
            return

        # normal zone: close + flip
        close_position(symbol, _magic(cfg), cfg.deviation_points)
        entered = enter_from_signal(cfg, symbol, sig)
        if entered:
            exposure.set_exposure({"zone_key": zk, "direction": sig.direction})
            cap.record_reversal_event(zk, sig.direction)
        return

    if not in_position:
        if paused:
            return  # no new entries from a paused pair
        # NOTE: a capped (not paused) zone still takes new entries like any
        # other right now - the earlier "scalp target" plan for capped
        # zones was purely a tighter-TP idea, which no longer applies now
        # that no TP is set anywhere. Flagged to the user as an open point.
        entered = enter_from_signal(cfg, symbol, sig)
        if entered:
            exposure.set_exposure({"zone_key": zk, "direction": sig.direction})
            cap.record_reversal_event(zk, sig.direction)


def zone_key_from_signal(sig: ReversalSignal) -> str:
    return f"{sig.zone_type}|{sig.tf}|{sig.identity}"


def update_trailing_stop(cfg: Config, symbol: str, zones, price: float) -> None:
    if not cfg.enable_trailing:
        return
    pos = managed_position(symbol, _magic(cfg))
    if pos is None:
        return
    direction = 1 if pos.type == mt5.ORDER_TYPE_BUY else -1

    target = second_nearest_zone_for_trailing(zones, direction, price)
    if target is None:
        return

    digits = _digits(symbol)
    candidate = round((target.low - cfg.sl_buffer) if direction == 1 else (target.high + cfg.sl_buffer), digits)

    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    min_stop = (info.trade_stops_level or 0) * info.point

    if direction == 1:
        if pos.sl > 0 and candidate <= pos.sl:
            return
        if candidate >= tick.bid - min_stop:
            return
    else:
        if pos.sl > 0 and candidate >= pos.sl:
            return
        if candidate <= tick.ask + min_stop:
            return

    result = mt5.order_send({
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": pos.ticket,
        "sl": candidate,
        "tp": pos.tp,
    })
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        _log(f"TRAIL FAILED: {result}")
    else:
        _log(f"TRAILED sl -> {candidate:.2f} (2nd-nearest {target.zone_type} {target.tf} zone)")


def check_zone_breaks(cap: ReversalCapTracker, zones, c: dict) -> None:
    for z in zones:
        if zone_broken(z, c):
            zk = zone_key(z)
            if cap.is_capped(zk) or cap.is_paused(zk):
                _log(f"ZONE BROKEN, cap/pause reset: {zk}")
            cap.record_zone_break(zk)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    connect(cfg)
    ensure_symbol(cfg.symbol)

    zone_watcher = ZoneWatcher(ZONE_STATE_PATH)
    cap = ReversalCapTracker(CAP_STATE_PATH)
    exposure = StateStore(EXPOSURE_STATE_PATH)

    last_candle_time: int | None = None
    prev_c: dict | None = None

    _log(f"Reversal trader started: symbol={cfg.symbol} trading_enabled={cfg.enable_trading} "
         f"magic={_magic(cfg)} lots={cfg.lots}")

    try:
        while True:
            rates = mt5.copy_rates_from_pos(cfg.symbol, mt5.TIMEFRAME_M1, 1, 1)
            if rates is not None and len(rates) > 0:
                c = candle_dict(rates[0])
                if c["time"] != last_candle_time:
                    try:
                        state = read_bridge_state()
                        zones = all_zone_refs(state)
                        tick = mt5.symbol_info_tick(cfg.symbol)
                        price = (tick.bid + tick.ask) / 2.0

                        check_zone_breaks(cap, zones, c)

                        for sig in scan_once(zone_watcher, state, prev_c, c):
                            zk = zone_key_from_signal(sig)
                            cap.record_zone_signal(zk)
                            _log(f"SIGNAL [{sig.tf}] {sig.zone_type} {sig.zone_low:.2f}-{sig.zone_high:.2f} "
                                 f"{'LONG' if sig.direction == 1 else 'SHORT'} {sig.signal_kind} "
                                 f"capped={cap.is_capped(zk)} paused={cap.is_paused(zk)}")
                            if cfg.enable_trading:
                                handle_signal(cfg, cfg.symbol, sig, cap, exposure)

                        if cfg.enable_trading:
                            update_trailing_stop(cfg, cfg.symbol, zones, price)

                        cap.save()
                    except FileNotFoundError as exc:
                        log.warning("Bridge file not ready yet: %s", exc)
                    prev_c = c
                    last_candle_time = c["time"]
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        log.info("Stopping on user interrupt.")
    finally:
        disconnect()


if __name__ == "__main__":
    run()
