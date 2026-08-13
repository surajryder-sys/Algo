"""Blocks an entire trade direction -- all of M1/M3/M5 together -- the
moment a position closes via a genuine SL hit, until a new OB in that
same direction appears on any of M1, M3, or M5. Distinct from
BlockedZoneStore (algo_v2/blocking.py), which blocks one specific
zone_key on one specific timeframe -- this is a coarser, direction-wide
safety net: "we just got stopped out going this way, don't try that
side again until something genuinely new supports it."

Applies uniformly to both new entries and the square-off mechanism in
main.py -- both draw from the same eligible-candidates list, so blocking
a direction there covers both with one check.

Block/release timing is pure broker-time timestamp comparison, no
wall-clock confirmation delay (unlike BlockedZoneStore's release, which
keeps a stability window for other reasons -- see release_stale_blocks
in main.py). Confirmed by spec: since a position can only ever open in a
direction once the zone/M5 bias already favors it, and this block's own
release condition is "a new same-direction OB appeared", the two
conditions are already correlated enough that an extra delay wouldn't
change anything here.

block_time is recorded from the closing deal's OWN time (MT5/broker
clock), not Python's wall-clock time.time() -- same clock-domain lesson
already learned from the zone-block release bug: comparing broker
bar-time (OB start_time) against wall-clock time made an already-current
OB spuriously look "newer than the block" purely from clock offset.
Recording and comparing entirely within broker time sidesteps that.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class DirectionBlockStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._blocked: dict[int, int] = {}  # direction (1/-1) -> block_time (broker epoch)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._blocked = {int(k): int(v) for k, v in data.get("blocked", {}).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._blocked = {}

    def _save(self) -> None:
        payload = {"blocked": {str(k): v for k, v in self._blocked.items()}}
        self._path.write_text(json.dumps(payload))

    def is_blocked(self, direction: int) -> bool:
        return direction in self._blocked

    def blocked_since(self, direction: int) -> Optional[int]:
        return self._blocked.get(direction)

    def block(self, direction: int, block_time: int) -> None:
        self._blocked[direction] = block_time
        self._save()

    def release(self, direction: int) -> None:
        if self._blocked.pop(direction, None) is not None:
            self._save()
