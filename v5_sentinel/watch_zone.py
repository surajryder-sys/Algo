"""Trend Manager's "watch zone" -- confirmed 2026-09-07, fixes a real gap
in the parent-gated M3 execution: the gate was rejecting exactly the
trades that matter most, an M3 flip at the START of a real trending
move, before the slower parent has caught up. M1->M3->M5->M15 lag is
normal market mechanics (a trend starts on the fastest timeframe and
works its way up), not an edge case -- the old design only ever caught
the SECOND chance (a trap-resolve after the parent had already turned),
missing the actual start of many real moves.

Instead of dropping an M3 FLIP the parent doesn't yet allow, it's parked
here with its own qualifying price (M3's flip-candle close). A parent
flipping into agreement later either fires the trade immediately (price
still close to that qualifying price) or arms a target to wait for
instead -- NOT a fixed number, a 45% retracement recomputed fresh every
cycle off the confirming parent's own CURRENT near trail line (the
anchor close stays fixed, but the near line keeps ratcheting, so the
target itself typically shallows over time the longer a real trend
holds -- this is what keeps the zone from missing the move on a big
impulsive parent candle, per the user's own worked examples 2026-09-07).

TRAP_RESOLVED events are NOT parked here -- "trap and resolve enters as
it is": that path keeps using the existing, unchanged pipeline.

Only ONE watch zone is ever active (Trend Manager runs one M3 execution
stream) -- arming a new one (a fresh, still-invalid M3 FLIP) replaces
whatever was there before.

Cancellation (either one drops the zone, no trade): M3 itself enters a
trap (fs_m3.watching is not None), or M3 flips again to a DIFFERENT
confirmed direction than the zone's own -- "it might turn bearish."

Recency: if MORE THAN ONE parent flip event qualifies while a zone is
pending (e.g. M5 confirms, then M15 also confirms later before any
pullback fired), the MOST RECENT one takes over -- both which parent's
data drives the pullback target, and the tag credited on the eventual
trade.

One trade per flip: firing a trade (either path) clears the zone -- no
second entry off the same original M3 flip.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

PULLBACK_GATE_POINTS = 2.0
PULLBACK_RETRACE_FRACTION = 0.45


@dataclass
class PendingPullback:
    parent_name: str          # "M5" or "M15" -- whichever confirmed
    parent_tf_code: str        # "5" or "15"
    anchor_close: float         # that parent's OWN flip-candle close -- FIXED once set
    source_bar_time: int        # that parent event's bar_time -- lets a later, more
                                  # recent parent event supersede this one


@dataclass
class WatchZone:
    direction: int
    qualifying_price: float     # M3's own flip-candle close
    m3_bar_time: int              # the M3 FLIP event this zone belongs to
    pending: Optional[PendingPullback] = None


class WatchZoneStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self.zone: Optional[WatchZone] = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            if raw is None:
                self.zone = None
                return
            pending_raw = raw.pop("pending", None)
            pending = PendingPullback(**pending_raw) if pending_raw else None
            self.zone = WatchZone(pending=pending, **raw)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self.zone = None

    def _save(self) -> None:
        self._path.write_text("null" if self.zone is None else json.dumps(asdict(self.zone)))

    def arm(self, direction: int, qualifying_price: float, m3_bar_time: int) -> None:
        self.zone = WatchZone(direction=direction, qualifying_price=qualifying_price, m3_bar_time=m3_bar_time)
        self._save()

    def cancel(self) -> None:
        if self.zone is not None:
            self.zone = None
            self._save()

    def set_pending(self, parent_name: str, parent_tf_code: str, anchor_close: float, source_bar_time: int) -> None:
        if self.zone is not None:
            self.zone.pending = PendingPullback(parent_name, parent_tf_code, anchor_close, source_bar_time)
            self._save()

    def clear(self) -> None:
        self.cancel()
