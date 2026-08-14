"""Per-(source_tf, direction) timestamp floor, raised the instant a zone on
that timeframe/direction gets cancelled or closed for ANY reason a block
would normally be created for -- manual pending cancellation, manual
position close, or the bot's own "zone turned against it" / "origin OB
invalidated" cancellation. Generalizes what started as an M1-only fix
(see git history) to all three timeframes and every cancellation path,
once it was confirmed the same hole exists everywhere, not just M1's
auto-cancel path.

The problem this closes: BlockedZoneStore.is_blocked() only blocks the
exact zone_key that was cancelled. If that specific OB later disappears
from the chart (gets mitigated/invalidated), the candidate-builder can
fall back to a DIFFERENT OB on the same timeframe -- even an older,
previously-untried one that was already sitting in the list before the
cancelled one even formed -- and since it's a different zone_key, the
per-zone block doesn't apply to it at all. From the outside this looks
like "I cancelled a trade, then once that zone got mitigated, an older
zone appeared and reset the block" -- the exact zone-level block never
actually covered the fallback zone in the first place.

This store closes that gap independently of BlockedZoneStore: any
candidate on (source_tf, direction) is rejected until one with a
start_time newer than the cancelled zone's own event_time actually
forms. Only ever ratchets forward per (source_tf, direction) -- no
explicit release needed, a later OB simply satisfies "newer than floor"
once one actually forms.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class ZoneCooldownStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._floor: dict[str, int] = {}   # "SOURCE_TF|DIRECTION" -> event_time
        self._load()

    def _key(self, source_tf: str, direction: int) -> str:
        return f"{source_tf}|{direction}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._floor = {str(k): int(v) for k, v in data.get("floor", {}).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._floor = {}

    def _save(self) -> None:
        payload = {"floor": self._floor}
        self._path.write_text(json.dumps(payload))

    def floor(self, source_tf: str, direction: int) -> Optional[int]:
        return self._floor.get(self._key(source_tf, direction))

    def raise_floor(self, source_tf: str, direction: int, event_time: int) -> None:
        """Only moves forward -- a cancellation always targets a currently
        live zone so this shouldn't ever see an out-of-order event_time,
        but guarding anyway: a stale/older cancellation must never lower
        an already-higher floor set by a more recent one."""
        key = self._key(source_tf, direction)
        current = self._floor.get(key)
        if current is None or event_time > current:
            self._floor[key] = event_time
            self._save()
