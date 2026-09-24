"""Persists, per MT5 ticket, the broker-side TP Scalper itself placed at
entry -- lets Exit Manager distinguish "Scalper's own untouched 1:1 TP"
(auto-close stays active, see broker.is_paused_by_manual_tp()) from "the
user has since changed or added a TP" (auto-close pauses, same convention
every other manager's manual-TP-pause already uses).

Written by scalper_main.py on every real fill (set()); read by every Exit
Manager component via WatchedSource.own_tp_lookup (see
exit_manager_config.py) -- a SEPARATE process from scalper_main.py, so
get() always re-reads the file fresh (never caches in memory) rather than
risk Exit Manager holding a stale, startup-time-only snapshot that would
never learn about a trade Scalper opens after Exit Manager's own process
started -- same "always read fresh" convention BlockStore/
LevelEligibilityStore already use for the identical reason.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class ScalperOwnTPStore:
    def __init__(self, path: str):
        self._path = Path(path)

    def _load(self) -> dict[str, float]:
        if not self._path.exists():
            return {}
        try:
            return {str(k): float(v) for k, v in json.loads(self._path.read_text()).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return {}

    def set(self, ticket: int, tp: float) -> None:
        data = self._load()
        data[str(ticket)] = tp
        self._path.write_text(json.dumps(data))

    def get(self, ticket: int) -> Optional[float]:
        return self._load().get(str(ticket))
