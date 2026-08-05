"""Manual-intervention blocking: if the user manually cancels a pending
order or closes a position (as opposed to the bot doing it, or an SL/TP/
stop-out exit), the exact OB zone that setup came from gets blocked from
re-entry -- one blocked zone per source timeframe (M1/M3/M5), mirroring the
old EA's per-timeframe block latch.

Released three ways:
  - automatically, once a NEW OB -- bullish or bearish, either direction --
    is detected on that same timeframe with a start_time after the block's
    own creation time (recorded below), confirmed stable for a few seconds
    -- see release_stale_blocks() / BLOCK_RELEASE_CONFIRM_SECONDS in
    main.py. Deliberately checks "has anything newer than the block itself
    appeared" rather than "is the current latest zone different from the
    blocked one": confirmed live that two different Python functions
    reading the same OB bridge data can transiently disagree on which zone
    is "latest," so comparing zone identities against each other was
    unreliable -- comparing against the block's own wall-clock timestamp
    only needs one fact to hold, regardless of which specific zone
    currently sits at the top of the bridge's list.
  - manually, via `python -m algo_v2.reset_block <M1|M3|M5|all>`
  - manually, via the RESET V2 M1/M3/M5 buttons on the bridge indicator's
    chart (its own row, separate from V1's buttons) -- see
    check_reset_requests() below

V1 (algo/) and this bot run on the same symbol/terminal simultaneously, so
the status file and reset-flag names below are namespaced "_V2" -- sharing
algo/blocking.py's BLOCK_STATUS.json or RESET_<tf>.flag files would mean
the two bots race each other over the same file and the chart's RESET
buttons would release both bots' blocks at once.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from ob_bridge.reader import bridge_root

RESET_FLAG_TIMEFRAMES = ("M1", "M3", "M5")
STATUS_FILE_NAME = "BLOCK_STATUS_V2.json"


class BlockedZoneStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._blocked: dict = {}      # source_tf -> zone_key
        self._reasons: dict = {}      # source_tf -> reason string
        self._block_times: dict = {}  # source_tf -> unix time (time.time()) the block was created
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
            self._block_times = data.get("block_times", {})
        else:
            # Older format: a flat {source_tf: zone_key} dict, no reasons.
            self._blocked = data
            self._reasons = {}
            self._block_times = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({
            "blocked": self._blocked,
            "reasons": self._reasons,
            "block_times": self._block_times,
        }))
        self.publish_status_file()

    def blocked_zone_key(self, source_tf: str) -> Optional[str]:
        return self._blocked.get(source_tf)

    def blocked_since(self, source_tf: str) -> Optional[float]:
        """Wall-clock time.time() the current block was created, or None
        if not blocked / the block predates this field (loaded from an
        older state file)."""
        return self._block_times.get(source_tf)

    def is_blocked(self, source_tf: str, zone_key: str) -> bool:
        return self._blocked.get(source_tf) == zone_key

    def block(self, source_tf: str, zone_key: str, reason: str = "unknown") -> None:
        self._blocked[source_tf] = zone_key
        self._reasons[source_tf] = reason
        self._block_times[source_tf] = time.time()
        self._save()

    def release(self, source_tf: str) -> Optional[str]:
        removed = self._blocked.pop(source_tf, None)
        self._reasons.pop(source_tf, None)
        self._block_times.pop(source_tf, None)
        if removed is not None:
            self._save()
        return removed

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
    """Polls for RESET_V2_<tf>.flag files, written by the bridge indicator's
    own RESET V2 M1/M3/M5 buttons (a separate row from V1's buttons)."""
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
