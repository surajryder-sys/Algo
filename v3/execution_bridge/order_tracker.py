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


# Raw tv_scraper timeframe code -> the readable label used in comments.
_TF_LABELS = {"240": "H4", "120": "H2", "60": "H1", "30": "M30", "15": "M15", "5": "M5", "3": "M3", "1": "M1"}
_LABEL_TO_TF = {label: code for code, label in _TF_LABELS.items()}
_DIRECTION_LETTERS = {"bull": "L", "bear": "S"}  # Long / Short
_LETTER_TO_DIRECTION = {letter: direction for direction, letter in _DIRECTION_LETTERS.items()}


def make_comment(prefix: str, parent_timeframe: str, exec_timeframe: str, direction: str, exec_start_time: int) -> str:
    """"V3-TM-M30-M3-L-1786929900" style ("parent-exec", pattern C),
    user's explicit choice 2026-08-20 -- adds the PARENT (bias-setting)
    timeframe alongside the exec (trigger/confirmation) one, so the
    comment alone shows both "what set the bias" and "what actually
    fired" without needing to check the logs. Still well under MT5's
    ~31-char limit even at the worst case (both timeframes at their
    longest, e.g. M30 both sides: "V3-TM-M30>M30-L-1787167440" is 26
    chars using pattern A's separator, same length under C's plain
    hyphens). Superseded the earlier "V3-TM-M5-L-..." (exec-only)
    format from 2026-08-18, which only ever showed the exec timeframe.

    Prefix distinguishes WHICH source decided this order -- "TM" (Trend
    Manager) or "RM" (Reversal Manager), each its own magic number too,
    but this is belt-and-suspenders for reading intent straight off the
    order in the MT5 terminal, and also how
    _find_matching_pending/_find_matching_position tell two sources'
    orders on the same symbol apart even if a timeframe/direction/
    start_time combination ever coincided.

    Direction as L(ong)/S(hort), not the first letter of "bull"/"bear"
    -- those are BOTH "b", so a single-letter encoding of the word
    itself can never actually distinguish them (caught 2026-08-18
    before this ever mattered in the previous "|"-separated format)."""
    parent_label = _TF_LABELS.get(parent_timeframe, parent_timeframe)
    exec_label = _TF_LABELS.get(exec_timeframe, exec_timeframe)
    letter = _DIRECTION_LETTERS[direction]
    return f"V3-{prefix}-{parent_label}-{exec_label}-{letter}-{exec_start_time}"


def parse_comment(comment: str) -> Optional[tuple]:
    """Returns (prefix, parent_timeframe, exec_timeframe, direction,
    exec_start_time) or None if this isn't a recognized comment shape
    (e.g. a manual close's exit deal sometimes doesn't preserve the
    entry comment -- caller falls back to the entry deal's own comment
    instead, see intervention.py)."""
    parts = comment.split("-")
    if len(parts) != 6 or parts[0] != "V3" or parts[1] not in ("TM", "RM"):
        return None
    _v3, prefix, parent_label, exec_label, letter, start_time_str = parts
    parent_timeframe = _LABEL_TO_TF.get(parent_label)
    exec_timeframe = _LABEL_TO_TF.get(exec_label)
    direction = _LETTER_TO_DIRECTION.get(letter)
    if parent_timeframe is None or exec_timeframe is None or direction is None:
        return None
    try:
        return prefix, parent_timeframe, exec_timeframe, direction, int(start_time_str)
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
