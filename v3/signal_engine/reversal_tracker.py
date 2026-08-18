"""Persisted state for Reversal Manager -- see reversal_manager.py's own
docstring for the full rule. Three things live here:

1. Per (symbol, timeframe, direction) bucket, a watermark -- same
   "never re-process the same retest twice" mechanism as
   v3/signal_engine/trade_tracker.py's, applied here to RETESTS instead
   of formations: a zone's retest only ever gets turned into a waiting
   event (HTF) or an immediate fire (M5) once, the first time it's
   observed non-virgin with a start_time newer than the bucket's
   watermark.

2. Per (symbol, direction), a list of currently "waiting" HTF retests
   (H4/H2/H1/M30/M15) -- each one records which zone retested, when,
   and its own top/btm (needed later for SL selection if multiple
   zones are waiting at once when confirmation actually fires -- "if a
   single candle retests multiple zones, whichever zone is at
   lowerside for buy trade decides sl"). Cleared entirely the moment
   either a confirmed LTF entry fires (consumed) or an opposite-
   direction LTF OB invalidates the whole setup.

3. Per symbol, at most one currently active REVERSAL trade -- separate
   from Trend Manager's own active trade, own magic number, can hold a
   position in the same OR opposite direction simultaneously (user's
   explicit confirmation 2026-08-17: "both can open same direction or
   opposite direction trades"). Closed via the entry OB's own
   mitigation, same stand-in-for-stopped-out convention used
   everywhere else in this system.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


def _bucket_key(symbol: str, timeframe: str, direction: str) -> str:
    return f"{symbol}|{timeframe}|{direction}"


@dataclass
class WaitingRetest:
    timeframe: str
    start_time: int
    top: float
    btm: float
    retest_time: float


@dataclass
class ActiveReversalTrade:
    direction: str
    entry_timeframe: str
    entry_start_time: int
    entry_price: float
    sl_price: Optional[float]
    mode: str  # "MARKET" or "PENDING"
    # PENDING -> FILLED once price crosses entry_price (mirrors
    # trade_tracker.py's own fill_pending) -- added 2026-08-18, before
    # this existed a real PENDING order that filled in MT5 would look
    # indistinguishable from "not yet placed" to Execution Bridge,
    # which would then place a SECOND pending order on top of an
    # already-filled position. MARKET starts FILLED immediately (fires
    # the instant it's decided, nothing to wait for).
    status: str = "PENDING"


class ReversalTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._watermarks: dict[str, int] = {}
        self._waiting: dict[str, dict[str, List[WaitingRetest]]] = {}  # symbol -> direction -> [WaitingRetest]
        self._active: dict[str, ActiveReversalTrade] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._watermarks = dict(raw.get("watermarks", {}))
        self._waiting = {
            symbol: {
                direction: [WaitingRetest(**w) for w in entries]
                for direction, entries in per_symbol.items()
            }
            for symbol, per_symbol in raw.get("waiting", {}).items()
        }
        self._active = {
            symbol: ActiveReversalTrade(**t)
            for symbol, t in raw.get("active_trades", {}).items()
        }

    def _save(self) -> None:
        out = {
            "watermarks": self._watermarks,
            "waiting": {
                symbol: {direction: [asdict(w) for w in entries] for direction, entries in per_symbol.items()}
                for symbol, per_symbol in self._waiting.items()
            },
            "active_trades": {symbol: asdict(t) for symbol, t in self._active.items()},
        }
        self._path.write_text(json.dumps(out))

    # -- watermark (retest de-dup) ---------------------------------------

    def is_new_retest(self, symbol: str, timeframe: str, direction: str, start_time: int) -> bool:
        key = _bucket_key(symbol, timeframe, direction)
        return start_time > self._watermarks.get(key, 0)

    def mark_retest_processed(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._watermarks.get(key, 0):
            self._watermarks[key] = start_time
        self._save()

    # -- waiting HTF retests ----------------------------------------------

    def add_waiting(self, symbol: str, direction: str, retest: WaitingRetest) -> None:
        self._waiting.setdefault(symbol, {}).setdefault(direction, []).append(retest)
        self._save()

    def get_waiting(self, symbol: str, direction: str) -> List[WaitingRetest]:
        return list(self._waiting.get(symbol, {}).get(direction, []))

    def clear_waiting(self, symbol: str, direction: str) -> None:
        if symbol in self._waiting and direction in self._waiting[symbol]:
            self._waiting[symbol][direction] = []
            self._save()

    # -- active trade -------------------------------------------------------

    def active_trade(self, symbol: str) -> Optional[ActiveReversalTrade]:
        return self._active.get(symbol)

    def open_trade(self, symbol: str, trade: ActiveReversalTrade) -> None:
        self._active[symbol] = trade
        self._save()

    def mark_filled(self, symbol: str) -> None:
        trade = self._active.get(symbol)
        if trade is not None:
            trade.status = "FILLED"
            self._save()

    def close_trade(self, symbol: str) -> None:
        self._active.pop(symbol, None)
        self._save()
