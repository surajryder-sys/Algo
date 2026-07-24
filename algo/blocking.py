"""Manual-intervention blocking: if the user manually cancels a pending
order or closes a position (as opposed to the bot doing it, or an SL/TP/
stop-out exit), the exact OB zone that setup came from gets blocked from
re-entry -- one blocked zone per source timeframe (M1/M3/M5), mirroring the
old EA's per-timeframe block latch.

Released two ways:
  - automatically, once a NEW OB forms in the same direction on that same
    timeframe (the blocked zone is no longer current, so the block is moot)
  - manually, via `python -m algo.reset_block <M1|M3|M5|all>`
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class BlockedZoneStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._blocked: dict = {}   # source_tf -> zone_key
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._blocked = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            self._blocked = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._blocked))

    def blocked_zone_key(self, source_tf: str) -> Optional[str]:
        return self._blocked.get(source_tf)

    def is_blocked(self, source_tf: str, zone_key: str) -> bool:
        return self._blocked.get(source_tf) == zone_key

    def block(self, source_tf: str, zone_key: str) -> None:
        self._blocked[source_tf] = zone_key
        self._save()

    def release(self, source_tf: str) -> Optional[str]:
        removed = self._blocked.pop(source_tf, None)
        if removed is not None:
            self._save()
        return removed

    def release_if_stale(self, source_tf: str, direction: int,
                         current_latest_zone_key: Optional[str]) -> None:
        """Auto-release only if the block belongs to the same direction and
        a genuinely different zone is now the latest for that direction."""
        blocked = self._blocked.get(source_tf)
        if blocked is None:
            return

        blocked_direction = int(blocked.split("|")[1])
        if blocked_direction != direction:
            return

        if current_latest_zone_key is not None and current_latest_zone_key != blocked:
            print(f"[BLOCK] auto-released {source_tf} block ({blocked}): new same-direction zone superseded it")
            self.release(source_tf)
