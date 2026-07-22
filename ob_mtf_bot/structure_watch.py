"""Continuous market-structure reader: the same cross-timeframe OB/FVG/
Dynamic-Zone read done manually during strategy discussion, re-run
automatically on every new M5 candle close and both printed and logged.

Read-only - does not place, modify, or close any orders.

Run with: python -m ob_mtf_bot.structure_watch
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

from ob_mtf_bot.bridge_reader import BridgeState, read_bridge_state
from ob_mtf_bot.config import load_config
from ob_mtf_bot.connection import connect, disconnect, ensure_symbol

log = logging.getLogger(__name__)

TIMEFRAMES = ["H4", "H2", "H1", "M30", "M15", "M5"]
CONFLUENCE_TOLERANCE_POINTS = 5.0
LOG_PATH = Path("structure_log.txt")


def _position(low: float, high: float, price: float) -> str:
    if low > price:
        return "ABOVE"
    if high < price:
        return "BELOW"
    return "AT-PRICE"


def _today_ohlc(symbol: str) -> dict | None:
    today_bar = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 1)
    if today_bar is None or len(today_bar) == 0:
        return None
    day_start = datetime.fromtimestamp(int(today_bar[0]["time"]), tz=timezone.utc)
    now = datetime.now(timezone.utc)

    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, day_start, now)
    if rates is None or len(rates) == 0:
        return None

    o = float(rates[0]["open"])
    h = float(max(r["high"] for r in rates))
    l = float(min(r["low"] for r in rates))
    c = float(rates[-1]["close"])
    direction = "BULLISH" if c > o else ("BEARISH" if c < o else "FLAT")
    return {"open": o, "high": h, "low": l, "close": c, "range": h - l, "direction": direction}


def _overlaps_or_near(a_low: float, a_high: float, b_low: float, b_high: float, tol: float) -> bool:
    return (a_low - tol) <= b_high and (b_low - tol) <= a_high


def _find_confluence(zones_by_tf: dict[str, list], direction: str) -> list[tuple[list[str], float, float]]:
    """Groups same-direction zones across timeframes that overlap or sit
    within CONFLUENCE_TOLERANCE_POINTS of each other; keeps only clusters
    where 2+ distinct timeframes agree."""
    flat = []
    for tf, zones in zones_by_tf.items():
        for z in zones:
            if z.direction == direction:
                flat.append((tf, z.low, z.high))
    flat.sort(key=lambda x: x[1])

    clusters: list[list[tuple[str, float, float]]] = []
    for item in flat:
        placed = False
        for cluster in clusters:
            if any(_overlaps_or_near(item[1], item[2], c[1], c[2], CONFLUENCE_TOLERANCE_POINTS) for c in cluster):
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])

    result = []
    for cluster in clusters:
        tfs = sorted({c[0] for c in cluster}, key=TIMEFRAMES.index)
        if len(tfs) < 2:
            continue
        lo = min(c[1] for c in cluster)
        hi = max(c[2] for c in cluster)
        result.append((tfs, lo, hi))
    return result


def build_report(state: BridgeState, symbol: str) -> str:
    tick = mt5.symbol_info_tick(symbol)
    price = (tick.bid + tick.ask) / 2.0

    lines = [f"===== {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC | {symbol} @ {price:.2f} ====="]

    today = _today_ohlc(symbol)
    if today:
        lines.append(
            f"Today: open={today['open']:.2f} high={today['high']:.2f} low={today['low']:.2f} "
            f"close={today['close']:.2f} range={today['range']:.2f} dir={today['direction']}"
        )

    dz = state.dynamic_zones
    if dz:
        lines.append(
            f"Dynamic Zones: upper5d={dz.zone1_upper_5d:.2f} upper10d={dz.zone2_upper_10d:.2f} "
            f"lower5d={dz.zone3_lower_5d:.2f} lower10d={dz.zone4_lower_10d:.2f} (prev day_open={dz.day_open:.2f})"
        )

    lines.append("")
    for tf in TIMEFRAMES:
        obs = sorted(state.order_blocks_for(tf), key=lambda z: -z.high)
        fvgs = sorted(state.fvgs_for(tf), key=lambda z: -z.high)
        lines.append(f"--- {tf} ---")
        for z in obs:
            lines.append(
                f"  OB  {z.direction:8s} {z.low:8.2f}-{z.high:8.2f} "
                f"virgin={str(z.virgin):5s} [{_position(z.low, z.high, price)}]"
            )
        for z in fvgs:
            lines.append(
                f"  FVG {z.direction:8s} {z.low:8.2f}-{z.high:8.2f} "
                f"retested={str(z.retested):5s} [{_position(z.low, z.high, price)}]"
            )

    zones_by_tf_ob = {tf: state.order_blocks_for(tf) for tf in TIMEFRAMES}
    zones_by_tf_fvg = {tf: state.fvgs_for(tf) for tf in TIMEFRAMES}

    lines.append("")
    lines.append("Confluence (OB, 2+ timeframes agreeing):")
    for direction in ("BULLISH", "BEARISH"):
        for tfs, lo, hi in _find_confluence(zones_by_tf_ob, direction):
            lines.append(f"  {direction:8s} {lo:.2f}-{hi:.2f}  [{'+'.join(tfs)}]")

    lines.append("Confluence (FVG, 2+ timeframes agreeing):")
    for direction in ("BULLISH", "BEARISH"):
        for tfs, lo, hi in _find_confluence(zones_by_tf_fvg, direction):
            lines.append(f"  {direction:8s} {lo:.2f}-{hi:.2f}  [{'+'.join(tfs)}]")

    lines.append("")
    return "\n".join(lines)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    connect(cfg)
    ensure_symbol(cfg.symbol)

    last_m5_time: int | None = None
    log.info("Structure watch started: symbol=%s, re-analyzing on every new M5 close", cfg.symbol)

    try:
        while True:
            bar = mt5.copy_rates_from_pos(cfg.symbol, mt5.TIMEFRAME_M5, 0, 1)
            if bar is not None and len(bar) > 0:
                bar_time = int(bar[0]["time"])
                if bar_time != last_m5_time:
                    last_m5_time = bar_time
                    try:
                        state = read_bridge_state()
                        report = build_report(state, cfg.symbol)
                        print(report)
                        with LOG_PATH.open("a", encoding="utf-8") as f:
                            f.write(report + "\n")
                    except FileNotFoundError as exc:
                        log.warning("Bridge file not ready yet: %s", exc)
            time.sleep(3)
    except KeyboardInterrupt:
        log.info("Stopping on user interrupt.")
    finally:
        disconnect()


if __name__ == "__main__":
    run()
