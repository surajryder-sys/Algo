"""Persists Trend Manager's trade-initiation AND entry-execution state --
see trend_manager.py's own docstring for the full rule this enforces.
Three things live here, all persisted to survive a restart:

1. Per (symbol, timeframe, direction) bucket, a "never trade backwards"
   watermark -- the start_time of the newest OB ever traded or blocked
   from that exact bucket. An OB is only ever eligible if its start_time
   is strictly newer than its own bucket's watermark. This is permanent
   and monotonic (only ever moves forward): explicit user requirement,
   2026-08-17 -- a stopped-out trade's OB can later be fully mitigated,
   and some OLDER OB further down the chart can reappear in tv_scraper's
   view looking "recent" (the exact same top-4 visibility churn bug
   behind several Alert Manager false positives). Without a permanent
   watermark, that reappeared-but-actually-old OB could fire a second,
   backwards-in-time trade off the same bucket. With it, it can't.
   Applies to BOTH parent timeframes AND trigger (execution) timeframes
   -- a trigger timeframe's own bucket only ever advances on an actual
   fill or a manual cancel (see mark_filled/close_trade), never merely
   for existing, since triggers aren't independently "traded" the way
   parents are.

2. Per symbol, at most one currently active trade: its parent OB
   (decides bias/direction) and, once one is chosen, its execution plan
   (which trigger timeframe, entry mode/price, SL). No real MT5 order
   tracking here -- Execution Bridge doesn't exist yet -- so this is
   Trend Manager's own signal-level bookkeeping, not a live position.
   Status is "PENDING" (a pending order proposed but not yet filled,
   still open to being cancelled-and-replaced by a better setup or
   flipped by an opposite parent OB) or "FILLED" (market-mode fires
   immediately; pending-mode fills once price crosses entry_price).
   "Closed" is detected via the entry OB's own mitigation (it fully
   disappears from the Data Bridge's zone store) as the best available
   stand-in for "stopped out" at this stage, per explicit user sign-off
   ("we can add that too... whatever is convenient").

3. A per-symbol watermark on real-world close events (see
   manual_event_watermark below) -- Execution Bridge writes to
   v3/execution_bridge/manual_events.py's own file the moment it
   detects a REAL close it didn't itself initiate: a manual cancel/
   close, OR a genuine SL/TP hit (broadened 2026-08-18 -- Trend
   Manager had no other way to learn a real SL hit happened, since its
   only other closure signal is the OB itself getting mitigated on the
   chart). This class only remembers the latest such event timestamp
   it's already reacted to per symbol, so the same event isn't
   processed twice across
   restarts or repeated polls.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from v3.tradingview_bot.zone_store import ZoneStore


def _bucket_key(symbol: str, timeframe: str, direction: str) -> str:
    return f"{symbol}|{timeframe}|{direction}"


@dataclass
class ActiveTrade:
    direction: str
    parent_timeframe: str
    parent_start_time: int
    exec_timeframe: Optional[str] = None
    exec_start_time: Optional[int] = None
    mode: Optional[str] = None          # "MARKET" or "PENDING"
    entry_price: Optional[float] = None
    sl_price: Optional[float] = None
    status: str = "AWAITING_TRIGGER"    # AWAITING_TRIGGER -> PENDING -> FILLED
    # True when this trade's exec_timeframe/exec_start_time came from an
    # ATR flip (trend_manager._try_fire_entry_atr_or_ob), not a real OB
    # -- exec_start_time in that case is the ATR event's own timestamp,
    # which never corresponds to an actual zone in the store. Added
    # 2026-08-20 after a real live bug: close_if_invalidated's zone
    # lookup on that synthetic timestamp always came back empty,
    # reading as "reference OB mitigated" and closing a USTEC trade 4
    # seconds after it opened. close_if_invalidated skips its check
    # entirely when this is True.
    exec_via_atr: bool = False


class TradeTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._active: dict[str, ActiveTrade] = {}  # symbol -> ActiveTrade
        self._watermarks: dict[str, int] = {}       # bucket_key -> start_time
        # Per-bucket cold-start seeding for PARENT timeframes only --
        # added 2026-08-19 after a real live incident: an M15 bearish OB
        # over an hour old (never previously watermarked in that bucket)
        # got treated as "the newest eligible parent" purely because
        # start_time > 0 (the default watermark), flipping bias off a
        # stale zone. v3's own copy of reversal_tracker.py's identical
        # mechanism (same root cause, same fix): the FIRST time a
        # parent-timeframe bucket is ever examined, whatever's currently
        # there gets seeded into the watermark rather than fired on --
        # only a genuinely NEW zone appearing after that first look can
        # ever set/flip bias from this bucket. See
        # trend_manager._newest_eligible_start_time for where this is
        # applied.
        self._seeded_buckets: set = set()
        self._manual_event_watermark: dict[str, float] = {}  # symbol -> last-reacted-to event time
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
        # was obviously seen/processed before this fix existed. Without
        # this, restarting against an existing state file would treat
        # every already-legitimately-touched bucket as "unseeded" and
        # re-seed it to the same value -- harmless in that specific
        # case, but the intent is "only genuinely untouched buckets get
        # the seed-not-fire treatment." Mirrors reversal_tracker.py's
        # own identical backfill.
        self._seeded_buckets |= set(self._watermarks.keys())
        self._active = {
            symbol: ActiveTrade(**trade)
            for symbol, trade in raw.get("active_trades", {}).items()
        }
        self._manual_event_watermark = dict(raw.get("manual_event_watermark", {}))

    def _save(self) -> None:
        out = {
            "watermarks": self._watermarks,
            "seeded_buckets": sorted(self._seeded_buckets),
            "active_trades": {symbol: asdict(t) for symbol, t in self._active.items()},
            "manual_event_watermark": self._manual_event_watermark,
        }
        self._path.write_text(json.dumps(out))

    def should_react_to_close_event(self, symbol: str, event_time: float) -> bool:
        """True (and records event_time as handled) only if this is a
        real-world close event (manual cancel/close, or a genuine SL/TP
        hit -- see manual_events.py's own docstring) Trend Manager
        hasn't already reacted to for this symbol -- idempotent across
        restarts/repeated polls, since Execution Bridge's event file is
        overwritten in place, not appended, and its own timestamp is
        the only signal of novelty."""
        last = self._manual_event_watermark.get(symbol, 0.0)
        if event_time <= last:
            return False
        self._manual_event_watermark[symbol] = event_time
        self._save()
        return True

    # -- watermark ------------------------------------------------------

    def is_eligible(self, symbol: str, timeframe: str, direction: str, start_time: int) -> bool:
        """True only if this exact bucket has never traded/blocked
        anything at or after this start_time -- the "never trade
        backwards" guarantee. See module docstring point 1."""
        key = _bucket_key(symbol, timeframe, direction)
        return start_time > self._watermarks.get(key, 0)

    def is_bucket_seeded(self, symbol: str, timeframe: str, direction: str) -> bool:
        """False only the very first time this exact PARENT-timeframe
        bucket is ever examined -- v3's own copy of
        ReversalTracker.is_bucket_seeded's identical reasoning. See
        __init__'s own docstring for the live incident this fixes."""
        return _bucket_key(symbol, timeframe, direction) in self._seeded_buckets

    def seed_bucket(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        """Marks this bucket seeded WITHOUT treating start_time as a
        fired/processed OB -- used by trend_manager.py's cold-start
        safeguard the first time a parent-timeframe bucket is ever
        examined, to skip whatever OB already exists rather than
        setting/flipping bias off it."""
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._watermarks.get(key, 0):
            self._watermarks[key] = start_time
        self._seeded_buckets.add(key)

    def _advance_watermark(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._watermarks.get(key, 0):
            self._watermarks[key] = start_time

    def mark_traded_only(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        """A new same-direction PARENT OB appeared while a trade is
        already open on this symbol -- block it from ever becoming a
        future parent later, without touching the currently active
        trade."""
        self._advance_watermark(symbol, timeframe, direction, start_time)
        self._save()

    # -- active trade lifecycle ------------------------------------------

    def active_trade(self, symbol: str) -> Optional[ActiveTrade]:
        return self._active.get(symbol)

    def set_parent(self, symbol: str, direction: str, parent_timeframe: str, parent_start_time: int) -> None:
        """Bias decided, no execution plan chosen yet. Does NOT advance
        any watermark -- a parent only gets permanently blocked once a
        trade actually fires or is manually cancelled off it (or an
        opposite parent flips the bias -- see close_trade)."""
        self._active[symbol] = ActiveTrade(
            direction=direction, parent_timeframe=parent_timeframe, parent_start_time=parent_start_time,
        )
        self._save()

    def propose_pending(self, symbol: str, exec_timeframe: str, exec_start_time: int,
                         entry_price: float, sl_price: Optional[float]) -> None:
        """A trigger timeframe's OB produced a PENDING-mode entry plan.
        Not blocked/watermarked yet -- only on fill or manual cancel."""
        trade = self._active[symbol]
        trade.exec_timeframe = exec_timeframe
        trade.exec_start_time = exec_start_time
        trade.mode = "PENDING"
        trade.entry_price = entry_price
        trade.sl_price = sl_price
        trade.status = "PENDING"
        self._save()

    def fill_market(self, symbol: str, exec_timeframe: str, exec_start_time: int,
                     entry_price: float, sl_price: Optional[float], via_atr: bool = False) -> None:
        """A trigger timeframe's OB (or, for USOIL/USTEC, an ATR flip --
        see via_atr) produced a MARKET-mode entry plan -- fills
        immediately (no waiting for price to reach anything). Blocks
        both the parent's and the exec timeframe's own buckets
        permanently, right now."""
        trade = self._active[symbol]
        trade.exec_timeframe = exec_timeframe
        trade.exec_start_time = exec_start_time
        trade.mode = "MARKET"
        trade.entry_price = entry_price
        trade.sl_price = sl_price
        trade.status = "FILLED"
        trade.exec_via_atr = via_atr
        self._advance_watermark(symbol, trade.parent_timeframe, trade.direction, trade.parent_start_time)
        self._advance_watermark(symbol, exec_timeframe, trade.direction, exec_start_time)
        self._save()

    def fill_pending(self, symbol: str) -> None:
        """Price has crossed the already-proposed pending entry price --
        transitions PENDING -> FILLED and blocks both buckets, same as
        fill_market."""
        trade = self._active[symbol]
        trade.status = "FILLED"
        self._advance_watermark(symbol, trade.parent_timeframe, trade.direction, trade.parent_start_time)
        self._advance_watermark(symbol, trade.exec_timeframe, trade.direction, trade.exec_start_time)
        self._save()

    def close_trade(self, symbol: str, block: bool = True) -> None:
        """Ends the active trade for this symbol -- used for: (a) the
        parent OB getting mitigated (see close_if_invalidated), (b) an
        opposite-direction parent OB flipping the bias, (c) eventually a
        manual cancel, once real orders exist. `block` permanently
        watermarks the parent (and exec timeframe, if one was ever
        chosen) so this exact setup can never be revisited -- true for
        all of the above EXCEPT a plain mitigation-of-a-still-
        AWAITING_TRIGGER parent (nothing was ever proposed off it, so
        there's nothing meaningful to permanently block beyond what
        mitigation itself already means)."""
        trade = self._active.pop(symbol, None)
        if trade is None:
            return
        if block:
            self._advance_watermark(symbol, trade.parent_timeframe, trade.direction, trade.parent_start_time)
            if trade.exec_timeframe is not None:
                self._advance_watermark(symbol, trade.exec_timeframe, trade.direction, trade.exec_start_time)
        self._save()

    def close_if_invalidated(self, symbol: str, store: ZoneStore) -> bool:
        """Checks whether the currently active trade's own reference OB
        (its exec OB if one's been chosen, else its parent OB) has been
        mitigated (fully removed from the Data Bridge's zone store) --
        the stand-in for "stopped out." Blocks on close (mitigation of a
        real, chosen setup means it played out and is done -- never
        worth revisiting). Returns True if a close happened.

        Skips entirely (returns False) when exec_via_atr is True -- an
        ATR-flip-confirmed trade's exec_start_time is the ATR event's
        own timestamp, not a real OB's, so there's no zone to look up at
        all; treating it as "not found -> mitigated" closed a real
        USTEC trade 4 seconds after it opened (confirmed live
        2026-08-20). Such a trade relies on SL/trailing, a bias flip, or
        a real manual/SL/TP close instead -- no reference-OB exit."""
        trade = self._active.get(symbol)
        if trade is None:
            return False
        if trade.exec_via_atr:
            return False
        ref_timeframe = trade.exec_timeframe or trade.parent_timeframe
        ref_start_time = trade.exec_start_time if trade.exec_timeframe else trade.parent_start_time
        zone = store.get(symbol, ref_timeframe, trade.direction, ref_start_time)
        if zone is not None:
            return False  # still live -- trade stays open
        self.close_trade(symbol, block=True)
        return True
