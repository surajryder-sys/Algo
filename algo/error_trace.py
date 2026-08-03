"""Persistent log of every exception the main loop catches and every
reconnect attempt, so a periodic error pattern can be proven instead of
inferred from indirect timing evidence.

Confirmed live (2026-08-03): a pending order got silently cancelled and
resubmitted by the terminal four times over 9 minutes, and every single
event lined up within about a minute of a ~1-second gap in the bias trace
log -- the signature of the main loop's except-and-reconnect path taking
slightly longer than a normal poll. That's strong circumstantial evidence
the periodic reconnect cycle itself is disturbing the terminal's pending-
order state, but nothing durable ever captured the actual exception
message to prove it -- this exists to catch that on the next occurrence.

One line per event, rotated by truncating to the tail half once the file
crosses ~1MB.
"""
from __future__ import annotations

import time
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent / "error_trace.log"
_MAX_BYTES = 1_000_000


def _rotate() -> None:
    try:
        text = _LOG_PATH.read_text()
    except OSError:
        return
    try:
        _LOG_PATH.write_text(text[len(text) // 2:])
    except OSError:
        pass


def log_error_event(kind: str, message: str) -> None:
    """kind: "ERROR" (run_once raised) or "RECOVERY_OK"/"RECOVERY_FAILED"."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {kind} {message}\n"
    try:
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _MAX_BYTES:
            _rotate()
        with open(_LOG_PATH, "a") as f:
            f.write(line)
    except OSError:
        pass
