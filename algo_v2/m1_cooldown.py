"""Per-direction timestamp floor for M1 entries, raised the instant a
resting M1 pending gets cancelled -- whether because its zone turned
against it OR its origin OB was invalidated (both reasons raise the same
floor; confirmed live neither implies the other is less suspect).

Confirmed live: once the specific OB a cancelled M1 pending was built from
disappears from the chart, build_m1_candidate falls back to whatever is
now the latest M1 OB in the list -- which can be an OLDER, previously-
untried rectangle sitting from before the reversal that just invalidated
the newer one. That's not a fresh setup, it's chasing the same choppy
patch of price with a different rectangle. The floor blocks ANY M1 OB in
that direction whose start_time doesn't postdate the invalidation event,
not just the one exact OB that got cancelled (that narrower per-zone
block already exists separately in BlockedZoneStore).

Only ever ratchets forward per direction -- never needs an explicit
release step; a later M1 OB simply satisfies "newer than floor" on its
own once one actually forms, the same way direction blocks release on a
newer OB rather than a timer.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class M1CooldownStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._floor: dict[int, int] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._floor = {int(k): int(v) for k, v in data.get("floor", {}).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._floor = {}

    def _save(self) -> None:
        payload = {"floor": {str(k): v for k, v in self._floor.items()}}
        self._path.write_text(json.dumps(payload))

    def floor(self, direction: int) -> Optional[int]:
        return self._floor.get(direction)

    def raise_floor(self, direction: int, event_time: int) -> None:
        """Only moves forward -- cancellation always targets the currently
        resting order so this shouldn't ever see an out-of-order event_time,
        but guarding anyway: a stale/older invalidation must never lower an
        already-higher floor set by a more recent one."""
        current = self._floor.get(direction)
        if current is None or event_time > current:
            self._floor[direction] = event_time
            self._save()
