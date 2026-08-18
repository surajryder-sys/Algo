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


def make_comment(prefix: str, exec_timeframe: str, direction: str, exec_start_time: int) -> str:
    """Well under MT5's ~31-char comment limit even spelled out in full
    (worst case "TM|240|bear|1787073660" is 22 chars). Prefix
    distinguishes WHICH source decided this order -- "TM" (Trend
    Manager) or "RM" (Reversal Manager), each its own magic number too,
    but this is belt-and-suspenders for reading intent straight off the
    order in the MT5 terminal, and also how
    _find_matching_pending/_find_matching_position tell two sources'
    orders on the same symbol apart even if a timeframe/direction/
    start_time combination ever coincided.

    Direction spelled out in full, not truncated to one letter --
    "bull"[0] and "bear"[0] are BOTH "b", so a single-letter encoding
    can never actually distinguish them (caught 2026-08-18 before this
    ever mattered: parse_comment's own decoded direction was unused by
    every caller so far, but would have silently always decoded as
    "bull" the moment anything actually read it back)."""
    return f"{prefix}|{exec_timeframe}|{direction}|{exec_start_time}"


def parse_comment(comment: str) -> Optional[tuple]:
    """Returns (prefix, exec_timeframe, direction, exec_start_time) or
    None if this isn't a recognized comment shape (e.g. a manual
    close's exit deal sometimes doesn't preserve the entry comment --
    caller falls back to the entry deal's own comment instead, see
    intervention.py)."""
    parts = comment.split("|")
    if len(parts) != 4 or parts[0] not in ("TM", "RM") or parts[2] not in ("bull", "bear"):
        return None
    prefix, timeframe, direction, start_time_str = parts
    try:
        return prefix, timeframe, direction, int(start_time_str)
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
