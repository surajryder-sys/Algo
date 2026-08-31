"""Standalone verification script -- NOT wired into any live trading path.

Polls both the existing MQL5-indicator bridge (v4.bridge.reader.read_atr_dual)
and the new chart-free Python computation (v4.bridge.native_trail.
read_native_trail_dual) for XAUUSD M1 side by side, logging every poll and
flagging any disagreement in trend or event_time (the two fields that
actually drive entry decisions) loudly. Run for a stretch spanning several
real flips before considering a cutover -- see v4/trend_manager/main.py's
own docstring for the eventual swap once this has been verified.

Run with: python -m v4.bridge.verify_native_trail
"""
from __future__ import annotations

import datetime
import time

import MetaTrader5 as mt5

from v4.bridge.native_trail import read_native_trail_dual
from v4.bridge.reader import read_atr_dual

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_SYMBOL = "XAUUSD"
_POLL_SECONDS = 5.0


def _log(msg: str) -> None:
    ts = datetime.datetime.now(tz=_IST).strftime("%H:%M:%S")
    print(f"[verify_native_trail {ts} IST] {msg}", flush=True)


def _fmt_et(t) -> str:
    return datetime.datetime.fromtimestamp(t, tz=_IST).strftime("%H:%M:%S") if t else "n/a"


def run_once() -> None:
    bridge = read_atr_dual(_SYMBOL, 1)
    native = read_native_trail_dual(_SYMBOL, 1)

    if bridge is None or bridge.is_stale():
        _log("bridge (MQL5 indicator) missing/stale this poll -- skipping comparison")
        return
    if native is None:
        _log("native (Python) computation returned None this poll -- skipping comparison")
        return

    mismatch = []
    if bridge.line1.trend != native.line1.trend or bridge.line1.event_time != native.line1.event_time:
        mismatch.append("line1")
    if bridge.line2.trend != native.line2.trend or bridge.line2.event_time != native.line2.event_time:
        mismatch.append("line2")
    if bridge.structure != native.structure:
        mismatch.append("structure")

    tag = "MISMATCH" if mismatch else "agree"
    _log(
        f"{tag} -- "
        f"bridge: l1={bridge.line1.trend:+d}@{bridge.line1.trail_stop:.3f}({_fmt_et(bridge.line1.event_time)}) "
        f"l2={bridge.line2.trend:+d}@{bridge.line2.trail_stop:.3f}({_fmt_et(bridge.line2.event_time)}) "
        f"struct={bridge.structure} | "
        f"native: l1={native.line1.trend:+d}@{native.line1.trail_stop:.3f}({_fmt_et(native.line1.event_time)}) "
        f"l2={native.line2.trend:+d}@{native.line2.trail_stop:.3f}({_fmt_et(native.line2.event_time)}) "
        f"struct={native.structure}"
        + (f"  <<< DIFFERS ON: {', '.join(mismatch)}" if mismatch else "")
    )


def main() -> None:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    mt5.symbol_select(_SYMBOL, True)
    _log(f"connected -- comparing {_SYMBOL} M1 bridge vs native every {_POLL_SECONDS:.0f}s")
    try:
        while True:
            run_once()
            time.sleep(_POLL_SECONDS)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
