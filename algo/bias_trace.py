"""Per-poll trace of raw and debounced M15/M5 bias inputs.

Exists specifically to answer "why did it close" with proof instead of
after-the-fact reconstruction. Confirmed live (2026-08-03): a BUY position
was force-closed via bias-flip while the user was watching the chart and
saw bias stay bullish with no bearish OB appearing at all. MT5's own trade
history can show WHEN a position closed and that the comment was "SMC
bias-flip close", but nothing about what compute_bias() actually read on
the poll that triggered it -- that data only exists for the instant the
poll runs, and is gone on the next one unless something writes it down.

One line per poll, rotated by truncating to the tail half once the file
crosses ~2MB, so it can run indefinitely without unbounded growth.
"""
from __future__ import annotations

import time
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent / "bias_trace.log"
_MAX_BYTES = 2_000_000


def _rotate() -> None:
    try:
        text = _LOG_PATH.read_text()
    except OSError:
        return
    try:
        _LOG_PATH.write_text(text[len(text) // 2:])
    except OSError:
        pass


def log_bias_poll(m15_raw, m5_raw, m15_debounced, m5_debounced, bias) -> None:
    line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"M15_raw=({m15_raw.direction},{m15_raw.origin_time}) "
            f"M5_raw=({m5_raw.direction},{m5_raw.origin_time}) "
            f"M15_debounced=({m15_debounced.direction},{m15_debounced.origin_time}) "
            f"M5_debounced=({m5_debounced.direction},{m5_debounced.origin_time}) "
            f"bias={bias.state.value}\n")
    try:
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _MAX_BYTES:
            _rotate()
        with open(_LOG_PATH, "a") as f:
            f.write(line)
    except OSError:
        pass
