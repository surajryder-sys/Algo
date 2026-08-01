"""Forensic order-attempt logging, independent of stdout/Start-Process
redirection -- which has proven unreliable all session (log files created
via a redirect handle have repeatedly, silently failed to receive any
output, for reasons never fully pinned down). This writes directly to its
own file, append mode, flushed after every single write, so a crash or
kill can never lose the last entries.

Exists specifically to get a definitive answer next time an order retcode
looks misleading (2026-08-02: a market order that returned non-DONE still
resulted in a real fill, and because the code trusted that retcode, the
zone never got marked traded and the next poll duplicated it). Every
attempt -- success or failure -- gets logged with the exact retcode, the
broker's own comment, and the ticket, so there's no more reconstructing
this after the fact from MT5's deal history.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parent
_LOG_PATH = _MODULE_DIR / "order_attempts.log"


def log_order_attempt(kind: str, zone_key: str, result, our_comment: str) -> None:
    """kind: "MARKET" or "PENDING". result: the OrderResult from broker.py."""
    line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} {kind} "
            f"zone={zone_key} ok={result.ok} retcode={result.retcode} "
            f"ticket={result.ticket} broker_comment={result.comment!r} "
            f"our_comment={our_comment!r}\n")
    try:
        with open(_LOG_PATH, "a") as f:
            f.write(line)
            f.flush()
    except OSError:
        pass
