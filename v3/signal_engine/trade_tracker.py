"""Persists Trend Manager's trade-initiation state -- see
trend_manager.py's own docstring for the full rule this exists to
enforce. Two things live here, both persisted to survive a restart:

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
   backwards-in-time trade off the same bucket. With it, it can't --
   the watermark doesn't care whether the OB currently LOOKS fresh, only
   whether its own start_time already got passed.

2. Per symbol, at most one currently active trade (direction + which
   OB triggered it). No real MT5 order tracking here -- Execution
   Bridge doesn't exist yet -- so this is Trend Manager's own
   signal-level bookkeeping, not a live position. "Closed" is detected
   via the entry OB's own mitigation (it fully disappears from the Data
   Bridge's zone store) as the best available stand-in for "stopped
   out" at this stage, per explicit user sign-off ("we can add that
   too... whatever is convenient").
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v3.tradingview_bot.zone_store import ZoneStore


def _bucket_key(symbol: str, timeframe: str, direction: str) -> str:
    return f"{symbol}|{timeframe}|{direction}"


@dataclass
class ActiveTrade:
    direction: str
    timeframe: str
    start_time: int


class TradeTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._active: dict[str, ActiveTrade] = {}  # symbol -> ActiveTrade
        self._watermarks: dict[str, int] = {}       # bucket_key -> start_time
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._watermarks = dict(raw.get("watermarks", {}))
        self._active = {
            symbol: ActiveTrade(**trade)
            for symbol, trade in raw.get("active_trades", {}).items()
        }

    def _save(self) -> None:
        out = {
            "watermarks": self._watermarks,
            "active_trades": {
                symbol: {"direction": t.direction, "timeframe": t.timeframe, "start_time": t.start_time}
                for symbol, t in self._active.items()
            },
        }
        self._path.write_text(json.dumps(out))

    def active_trade(self, symbol: str) -> Optional[ActiveTrade]:
        return self._active.get(symbol)

    def active_direction(self, symbol: str) -> Optional[str]:
        trade = self._active.get(symbol)
        return trade.direction if trade else None

    def is_eligible(self, symbol: str, timeframe: str, direction: str, start_time: int) -> bool:
        """True only if this exact bucket has never traded/blocked
        anything at or after this start_time -- the "never trade
        backwards" guarantee. See module docstring point 1."""
        key = _bucket_key(symbol, timeframe, direction)
        return start_time > self._watermarks.get(key, 0)

    def open_trade(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        """Initiates a new trade off this OB -- only call when
        active_direction(symbol) is None. Also advances the watermark,
        so this exact OB (or anything older in its bucket) can never
        fire again even after the trade eventually closes."""
        self._active[symbol] = ActiveTrade(direction=direction, timeframe=timeframe, start_time=start_time)
        self._advance_watermark(symbol, timeframe, direction, start_time)
        self._save()

    def mark_traded_only(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        """A new same-direction OB appeared while a trade is already
        open on this symbol -- block it from ever initiating its own
        trade later, without touching the currently active trade.
        Explicit user rule: "if we are in a buy trade, and one more ob
        appears on bullish side, then consider that also traded"."""
        self._advance_watermark(symbol, timeframe, direction, start_time)
        self._save()

    def _advance_watermark(self, symbol: str, timeframe: str, direction: str, start_time: int) -> None:
        key = _bucket_key(symbol, timeframe, direction)
        if start_time > self._watermarks.get(key, 0):
            self._watermarks[key] = start_time

    def close_if_invalidated(self, symbol: str, store: ZoneStore) -> bool:
        """Checks whether the currently active trade's own entry OB has
        been mitigated (fully removed from the Data Bridge's zone
        store) -- see module docstring point 2 for why this is the
        stand-in for "stopped out" at this stage. Clears active_trade
        and returns True if so; no-op (returns False) otherwise,
        including when there's no active trade at all for this symbol."""
        trade = self._active.get(symbol)
        if trade is None:
            return False
        zone = store.get(symbol, trade.timeframe, trade.direction, trade.start_time)
        if zone is not None:
            return False  # still live -- trade stays open
        del self._active[symbol]
        self._save()
        return True
