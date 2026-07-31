"""Manual-intervention blocking: if the user manually cancels a pending
order or closes a position (as opposed to the bot doing it, or an SL/TP/
stop-out exit), the exact OB zone that setup came from gets blocked from
re-entry -- one blocked zone per source timeframe (M1/M3/M5), mirroring the
old EA's per-timeframe block latch.

Released two ways:
  - automatically, once a NEW OB forms in the same direction on that same
    timeframe (the blocked zone is no longer current, so the block is moot)
  - manually, via `python -m algo_v2.reset_block <M1|M3|M5|all>`

V1 (algo/) and this bot run on the same symbol/terminal simultaneously, so
the status file and reset-flag names below are namespaced "_V2" -- sharing
algo/blocking.py's BLOCK_STATUS.json or RESET_<tf>.flag files would mean
the two bots race each other over the same file and the chart's RESET
buttons would release both bots' blocks at once. The chart isn't wired
with a second set of V2 buttons yet, so check_reset_requests() below is a
no-op today (nothing writes RESET_V2_<tf>.flag) -- ready for that later.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ob_bridge.reader import bridge_root

RESET_FLAG_TIMEFRAMES = ("M1", "M3", "M5")
STATUS_FILE_NAME = "BLOCK_STATUS_V2.json"


class BlockedZoneStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._blocked: dict = {}   # source_tf -> zone_key
        self._reasons: dict = {}  # source_tf -> reason string
        self._load()
        self.publish_status_file()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return

        if "blocked" in data:
            self._blocked = data.get("blocked", {})
            self._reasons = data.get("reasons", {})
        else:
            # Older format: a flat {source_tf: zone_key} dict, no reasons.
            self._blocked = data
            self._reasons = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({"blocked": self._blocked, "reasons": self._reasons}))
        self.publish_status_file()

    def blocked_zone_key(self, source_tf: str) -> Optional[str]:
        return self._blocked.get(source_tf)

    def is_blocked(self, source_tf: str, zone_key: str) -> bool:
        return self._blocked.get(source_tf) == zone_key

    def block(self, source_tf: str, zone_key: str, reason: str = "unknown") -> None:
        self._blocked[source_tf] = zone_key
        self._reasons[source_tf] = reason
        self._save()

    def release(self, source_tf: str) -> Optional[str]:
        removed = self._blocked.pop(source_tf, None)
        self._reasons.pop(source_tf, None)
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

    def publish_status_file(self) -> None:
        """Writes a small status file -- separate from V1's -- for a future
        V2-specific chart display, without touching V1's BLOCK_STATUS.json."""
        status = {
            tf: {
                "blocked": tf in self._blocked,
                "reason": self._reasons.get(tf),
                "zone_key": self._blocked.get(tf),
            }
            for tf in RESET_FLAG_TIMEFRAMES
        }

        final_path = bridge_root() / STATUS_FILE_NAME
        tmp_path = final_path.with_suffix(".json.tmp")
        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(status, separators=(",", ":")))
            tmp_path.replace(final_path)
        except OSError:
            pass


def check_reset_requests(blocked: BlockedZoneStore) -> None:
    """Polls for RESET_V2_<tf>.flag files -- nothing writes these yet since
    the chart indicator only has one set of RESET buttons (V1's). Kept as a
    no-op hook so a future second set of V2 buttons can just start writing
    these flags without any Python-side change."""
    for tf in RESET_FLAG_TIMEFRAMES:
        flag_path = bridge_root() / f"RESET_V2_{tf}.flag"
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
