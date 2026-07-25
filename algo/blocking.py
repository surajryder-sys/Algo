"""Manual-intervention blocking: if the user manually cancels a pending
order or closes a position (as opposed to the bot doing it, or an SL/TP/
stop-out exit), the exact OB zone that setup came from gets blocked from
re-entry -- one blocked zone per source timeframe (M1/M3/M5), mirroring the
old EA's per-timeframe block latch.

Released three ways:
  - automatically, once a NEW OB forms in the same direction on that same
    timeframe (the blocked zone is no longer current, so the block is moot)
  - manually, via `python -m algo.reset_block <M1|M3|M5|all>`
  - manually, via the RESET M1/M3/M5 buttons on the bridge indicator's chart
    (see check_reset_requests() below)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ob_bridge.reader import bridge_root

RESET_FLAG_TIMEFRAMES = ("M1", "M3", "M5")


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


def check_reset_requests(blocked: BlockedZoneStore) -> None:
    """Polls for RESET_<tf>.flag files the indicator's chart buttons write,
    releases the corresponding block, and clears the flag. Deliberately not
    gated behind enable_trading -- a manual reset should always take effect
    immediately, dry-run or not."""
    for tf in RESET_FLAG_TIMEFRAMES:
        flag_path = bridge_root() / f"RESET_{tf}.flag"
        if not flag_path.exists():
            continue

        released = blocked.release(tf)
        if released is not None:
            print(f"[BLOCK] chart reset button released {tf} block on {released}")
        else:
            print(f"[BLOCK] chart reset button pressed for {tf} (no active block)")

        try:
            flag_path.unlink()
        except OSError:
            pass
