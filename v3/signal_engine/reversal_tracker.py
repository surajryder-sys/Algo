"""Persisted state for Reversal Manager -- see reversal_manager.py's own
docstring for the full rule. Three things live here:

1. Per (symbol, timeframe, direction) bucket, a watermark -- same
   "never re-process the same retest twice" mechanism as
   v3/signal_engine/trade_tracker.py's, applied here to RETESTS instead
   of formations: a zone's retest only ever gets turned into a waiting
   event (HTF) or an immediate fire (M5) once, the first time it's
   observed non-virgin with a start_time newer than the bucket's
   watermark. Alongside it, seeded_buckets (per-bucket, not a single
   whole-file flag) tracks which buckets have ever been examined at
   all -- confirmed live 2026-08-18 (twice) that a bucket seen for the
   first time must SEED its watermark to whatever's currently already
   retested rather than fire on it, or it fires on a real but
   arbitrarily stale retest the moment that bucket becomes active for
   the first time (a whole-file "cold start" flag alone isn't enough --
   a bucket that just hadn't been touched yet looks identical to a
   fresh start even in a state file that's been running a while).

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
        # Which (symbol, timeframe, direction) buckets have ever been
        # examined at least once -- separate from _watermarks itself
        # (whose default-0 value is indistinguishable from "genuinely
        # processed a retest at time 0"). Added 2026-08-18 after TWO
        # separate live incidents: a whole-file "cold start" flag caught
        # the first one (day-old BTCUSD/ETHUSD bull retests firing the
        # moment this process first ran continuously) but NOT the
        # second -- a bucket that simply hadn't been touched yet (the
        # BEAR direction, never having fired before) fired on a real but
        # WEEK-OLD retest on the very next restart, since the file
        # already existed so the whole-file flag no longer applied. This
        # per-bucket set is the actual fix: reversal_manager.py's
        # _newest_retested_zone seeds (not fires on) any bucket seen for
        # the first time, regardless of whether the file as a whole is
        # new.
        self._seeded_buckets: set = set()
        self._waiting: dict[str, dict[str, List[WaitingRetest]]] = {}  # symbol -> direction -> [WaitingRetest]
        self._active: dict[str, ActiveReversalTrade] = {}
        self._manual_event_watermark: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._watermarks = dict(raw.get("watermarks", {}))
        self._seeded_buckets = set(raw.get("seeded_buckets", []))
        # Backfill -- any bucket that already has a real watermark entry
        # was obviously seen/processed under the pre-2026-08-18 scheme,
        # before seeded_buckets existed at all. Without this, restarting
        # against an existing state file (any bucket that had already
        # legitimately fired before this fix) would look "unseeded" and
        # get its watermark needlessly re-seeded to the same value --
        # harmless in that specific case, but the intent is "only
        # genuinely untouched buckets get the seed-not-fire treatment."
        self._seeded_buckets |= set(self._watermarks.keys())
        self._manual_event_watermark = dict(raw.get("manual_event_watermark", {}))
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
            "seeded_buckets": sorted(self._seeded_buckets),
            "waiting": {
                symbol: {direction: [asdict(w) for w in entries] for direction, entries in per_symbol.items()}
                for symbol, per_symbol in self._waiting.items()
            },
            "active_trades": {symbol: asdict(t) for symbol, t in self._active.items()},
            "manual_event_watermark": self._manual_event_watermark,
        }
        self._path.write_text(json.dumps(out))

    def should_react_to_close_event(self, symbol: str, event_time: float) -> bool:
        """Mirrors trade_tracker.TradeTracker's own -- True (and records
        event_time as handled) only if this is a real-world close event
        (manual cancel/close, or a genuine SL/TP hit) Reversal Manager
        hasn't already reacted to for this symbol. Added 2026-08-18
        after a real SL hit on a Reversal Manager position left its own
        active_trade record showing FILLED forever, with nothing real
        behind it, and Execution Bridge kept re-opening a brand new
        position for it every cycle since Reversal Manager never
        learned the original had closed."""
        last = self._manual_event_watermark.get(symbol, 0.0)
        if event_time <= last:
            return False
        self._manual_event_watermark[symbol] = event_time
        self._save()
        return True

    # -- watermark (retest de-dup) ---------------------------------------

    def is_new_retest(self, symbol: str, timeframe: str, direction: str, start_time: int) -> bool:
        key = _bucket_key(symbol, timeframe, direction)
        return start_time > self._watermarks.get(key, 0)

    def is_bucket_seeded(self, symbol: str, timeframe: str, direction: str) -> bool:
        """False only the very first time this exact bucket is ever
        examined -- see the class docstring's own note on why a
        per-bucket check (not just a whole-file cold-start flag) is
        needed: a bucket that simply hadn't fired before (e.g. one
        direction while only the other had ever been active) looks
        identical to a genuine cold start otherwise, even in a state
        file that's been running for a while."""
        return _bucket_key(symbol, timeframe, direction) in self._seeded_buckets

    def seed_bucket(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        """Marks this bucket seeded WITHOUT treating start_time as a
        fired/processed retest event -- used by
        reversal_manager.py's cold-start safeguard the first time a
        bucket is ever examined, to skip whatever retest already
        exists rather than firing on it."""
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._watermarks.get(key, 0):
            self._watermarks[key] = start_time
        self._seeded_buckets.add(key)
        self._save()

    def mark_retest_processed(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._watermarks.get(key, 0):
            self._watermarks[key] = start_time
        self._seeded_buckets.add(key)
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
