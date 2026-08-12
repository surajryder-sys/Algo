"""Manual-intervention blocking: if the user manually cancels a pending
order or closes a position (as opposed to the bot doing it, or an SL/TP/
stop-out exit), the exact OB zone that setup came from gets blocked from
re-entry -- one blocked zone per source timeframe (M1/M3/M5). Same concept
and release rules as algo_v2/blocking.py.

Deliberately DROPS that module's chart-button reset feature
(check_reset_requests / RESET_V2_<tf>.flag files, published via MT5 Common
Files): those buttons live on the MT5 OB-bridge indicator's own chart,
which has nothing to do with this bot's data source (TradingView webhooks,
no MT5 indicator chart at all). The only manual-release path here is the
CLI: `python -m algo_v2_tv_xauusd.reset_block <M1|M3|M5|all>`.

Released automatically the same way as algo_v2: once a NEW OB (either
direction) is detected on that same timeframe with a start_time after the
block's own creation time, confirmed stable for a few seconds -- see
release_stale_blocks() in main.py.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


class BlockedZoneStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._blocked: dict = {}      # source_tf -> zone_key
        self._reasons: dict = {}      # source_tf -> reason string
        self._block_times: dict = {}  # source_tf -> unix time (time.time()) the block was created
        self._load()

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
