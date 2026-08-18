"""Execution Bridge's own persisted state -- separate from, and
downstream of, v3/signal_engine/trade_tracker.py's. Trend Manager's own
state (trend_manager_trade_state.json) is the source of truth for WHAT
should be open/pending; this file tracks the real MT5 ticket that
currently represents that decision, so execution_bridge.py can tell
"already placed, nothing to do" apart from "Trend Manager's proposal
changed, cancel and replace" without re-querying MT5 history every
cycle just to figure that out.

Also carries `expected_cancellations` -- tickets Execution Bridge itself
just cancelled (added at the same call site that invokes
broker.cancel_pending_order, BEFORE the next poll's diff runs) -- so a
self-initiated cancel-and-replace is never mistaken for the user
manually pulling out. Same pattern as algo_v2/main.py's RuntimeState
field of the same name.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TrackedOrder:
    kind: str          # "PENDING" or "POSITION"
    ticket: int
    direction: str      # "bull" / "bear"
    exec_timeframe: str
    exec_start_time: int


def make_comment(exec_timeframe: str, direction: str, exec_start_time: int) -> str:
    """Short enough to survive MT5's ~31-char comment limit with room to
    spare. "TM" prefix distinguishes Trend Manager's own orders from any
    other bot sharing this account/magic-number space (though magic
    number alone already does that -- this is just belt-and-suspenders
    for reading intent straight off the order in the MT5 terminal)."""
    return f"TM|{exec_timeframe}|{direction[0]}|{exec_start_time}"


def parse_comment(comment: str) -> Optional[tuple]:
    """Returns (exec_timeframe, direction, exec_start_time) or None if
    this isn't one of ours (e.g. a manual close's exit deal sometimes
    doesn't preserve the entry comment -- caller falls back to the
    entry deal's own comment instead, see intervention.py)."""
    parts = comment.split("|")
    if len(parts) != 4 or parts[0] != "TM":
        return None
    _, timeframe, direction_letter, start_time_str = parts
    direction = "bull" if direction_letter == "b" else "bear"
    try:
        return timeframe, direction, int(start_time_str)
    except ValueError:
        return None


class OrderTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._tracked: dict[str, TrackedOrder] = {}
        self._expected_cancellations: set = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._tracked = {symbol: TrackedOrder(**t) for symbol, t in raw.get("tracked", {}).items()}
        self._expected_cancellations = set(raw.get("expected_cancellations", []))

    def _save(self) -> None:
        out = {
            "tracked": {symbol: asdict(t) for symbol, t in self._tracked.items()},
            "expected_cancellations": list(self._expected_cancellations),
        }
        self._path.write_text(json.dumps(out))

    def get(self, symbol: str) -> Optional[TrackedOrder]:
        return self._tracked.get(symbol)

    def set(self, symbol: str, order: TrackedOrder) -> None:
        self._tracked[symbol] = order
        self._save()

    def clear(self, symbol: str) -> None:
        self._tracked.pop(symbol, None)
        self._save()

    def mark_expected_cancellation(self, ticket: int) -> None:
        self._expected_cancellations.add(ticket)
        self._save()

    def was_expected_cancellation(self, ticket: int) -> bool:
        """Consumes the entry if present -- an expected cancellation is
        only ever relevant for the one poll where it's actually
        observed disappearing."""
        if ticket in self._expected_cancellations:
            self._expected_cancellations.discard(ticket)
            self._save()
            return True
        return False
