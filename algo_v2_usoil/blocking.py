"""Manual-intervention blocking: if the user manually cancels a pending
order or closes a position (as opposed to the bot doing it, or an SL/TP/
stop-out exit), the exact OB zone that setup came from gets blocked from
re-entry -- one blocked zone per source timeframe (M5, M15 for now).

Released three ways:
  - automatically, once a NEW OB -- bullish or bearish, either direction --
    is detected on that same timeframe with a start_time after the block's
    own creation time (recorded below), confirmed stable for a few seconds
    -- see release_stale_blocks() / BLOCK_RELEASE_CONFIRM_SECONDS in
    main.py.
  - manually, via `python -m algo_v2_usoil.reset_block <M5|M15|all>`
  - manually, via the RESET V2 USOIL M5/M15 buttons on the USOIL bridge
    indicator's chart -- see check_reset_requests() below

IMPORTANT: this bot's status/reset filenames are namespaced "_USOIL",
distinct from algo_v2's (XAUUSD) "BLOCK_STATUS_V2.json" /
"RESET_V2_<tf>.flag". Both bots read/write through the same shared MT5
Common Files bridge folder (see ob_bridge.reader.bridge_root) even when
running on separate charts/symbols, so sharing algo_v2's filenames here
would mean this bot's blocks and reset buttons silently stomp on
(and get stomped on by) the XAUUSD V2 bot's -- confirmed by reading
algo_v2/blocking.py and the MQL5 indicator before building this copy.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from ob_bridge.reader import bridge_root

RESET_FLAG_TIMEFRAMES = ("M5", "M15")
STATUS_FILE_NAME = "BLOCK_STATUS_V2_USOIL.json"
RESET_FLAG_PREFIX = "RESET_V2_USOIL_"


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
        """Writes a small status file -- separate from algo_v2's -- for a
        future chart display, without touching the XAUUSD bot's file."""
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
    """Polls for RESET_V2_USOIL_<tf>.flag files, written by the USOIL
    bridge indicator's own RESET button."""
    for tf in RESET_FLAG_TIMEFRAMES:
        flag_path = bridge_root() / f"{RESET_FLAG_PREFIX}{tf}.flag"
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
