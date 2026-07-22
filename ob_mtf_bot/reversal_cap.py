"""Two independent whipsaw brakes, both JSON-persisted so state survives a
restart:

  - Single-zone cap: a zone's counter increments on every zone_watcher
    signal fired there (regardless of whether a trade was open at the
    time). At SINGLE_ZONE_CAP fires without the zone breaking, it's marked
    capped: new trades from it are still taken but with a tight scalp
    target instead of the normal far-zone target; an already-open trade at
    a capped zone only closes (no flip) on a STRONG opposing signal there
    (wick_rejection/engulfing) - a plain rejection_close is ignored.
    Resets the moment the zone actually breaks.

  - Two-zone ping-pong: tracks the sequence of TRADE-CHANGING reversal
    events (entries/flips, not every raw signal). If the last
    PING_PONG_EVENTS_NEEDED events alternate between exactly the same two
    zones (PING_PONG_ROUND_TRIPS full round trips), both zones pause -
    no new trades from either - until one of them breaks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SINGLE_ZONE_CAP = 4
PING_PONG_ROUND_TRIPS = 3
PING_PONG_EVENTS_NEEDED = PING_PONG_ROUND_TRIPS * 2


@dataclass
class ReversalCapState:
    reject_counts: dict[str, int] = field(default_factory=dict)
    capped_zones: set[str] = field(default_factory=set)
    reversal_history: list[list] = field(default_factory=list)   # [[zone_key, direction], ...] oldest first
    paused_pairs: dict[str, str] = field(default_factory=dict)    # zone_key -> partner zone_key


class ReversalCapTracker:
    def __init__(self, path: Path):
        self.path = path
        self.state = ReversalCapState()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self.state = ReversalCapState(
            reject_counts=raw.get("reject_counts", {}),
            capped_zones=set(raw.get("capped_zones", [])),
            reversal_history=raw.get("reversal_history", []),
            paused_pairs=raw.get("paused_pairs", {}),
        )

    def save(self) -> None:
        self.path.write_text(json.dumps({
            "reject_counts": self.state.reject_counts,
            "capped_zones": sorted(self.state.capped_zones),
            "reversal_history": self.state.reversal_history[-PING_PONG_EVENTS_NEEDED:],
            "paused_pairs": self.state.paused_pairs,
        }, indent=2))

    def record_zone_signal(self, zone_key: str) -> None:
        """Call every time zone_watcher fires a signal for this zone."""
        count = self.state.reject_counts.get(zone_key, 0) + 1
        self.state.reject_counts[zone_key] = count
        if count >= SINGLE_ZONE_CAP:
            self.state.capped_zones.add(zone_key)

    def record_zone_break(self, zone_key: str) -> None:
        """Call when price closes decisively through a zone - resets its
        cap and releases it (and its partner) from any paused pair."""
        self.state.reject_counts.pop(zone_key, None)
        self.state.capped_zones.discard(zone_key)
        partner = self.state.paused_pairs.pop(zone_key, None)
        if partner:
            self.state.paused_pairs.pop(partner, None)

    def record_reversal_event(self, zone_key: str, direction: int) -> None:
        """Call every time a trade-changing reversal event happens (a fresh
        entry from flat, or a close+flip)."""
        self.state.reversal_history.append([zone_key, direction])
        self.state.reversal_history = self.state.reversal_history[-PING_PONG_EVENTS_NEEDED:]

        if len(self.state.reversal_history) < PING_PONG_EVENTS_NEEDED:
            return

        zones_involved = {e[0] for e in self.state.reversal_history}
        if len(zones_involved) != 2:
            return

        directions_by_zone: dict[str, int] = {}
        for zk, d in self.state.reversal_history:
            if zk in directions_by_zone and directions_by_zone[zk] != d:
                return  # not a clean alternation - same zone flip-flopped direction
            directions_by_zone[zk] = d

        alternates = all(
            self.state.reversal_history[i][0] != self.state.reversal_history[i + 1][0]
            for i in range(len(self.state.reversal_history) - 1)
        )
        if not alternates:
            return

        a, b = sorted(zones_involved)
        self.state.paused_pairs[a] = b
        self.state.paused_pairs[b] = a

    def is_capped(self, zone_key: str) -> bool:
        return zone_key in self.state.capped_zones

    def is_paused(self, zone_key: str) -> bool:
        return zone_key in self.state.paused_pairs
