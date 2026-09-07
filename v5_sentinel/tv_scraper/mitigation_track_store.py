"""Persists the mitigation-detection tracking state scraper.py's
_apply_direction() needs across polls -- which price_keys were seen last
poll (and under what start_time), how many consecutive polls each has been
missing, and the pending 2-poll confirmation gates for formed/retested
hints. See _apply_direction's own docstring for what each of these means
and why the 2-poll debounce exists at all.

Before this store existed, this state lived ONLY in scraper.py's own
in-process module-level dicts (_last_seen / _missing_streak /
_pending_retest / _pending_formed) -- fine as long as the process never
restarts, but confirmed live to cause a real, silent bug once
ZoneStore.apply_mitigated() started DELETING zones instead of just
flagging them (see that method's own docstring): any zone already sitting
in the persisted ZoneStore, but not in the very first poll's top-4 view
after a restart, was never in the fresh process's "previously seen" set --
so the "missing for 2 consecutive polls -> mitigate" check could never
fire for it again. It just sat there forever, immune to cleanup, on every
restart that happened while it was out of view. Confirmed live: 4
genuinely weeks-stale H1 zones survived across this session's many
restarts specifically because of this. Persisting the SAME state this
store already tracked in-memory closes that gap -- a restart now resumes
exactly where the previous process left off, so a zone that was already
mid-debounce (or already known-missing) before a restart stays that way
after it, instead of getting a clean slate it never earned."""
from __future__ import annotations

import json
from pathlib import Path


class MitigationTrackStore:
    def __init__(self, path: str):
        self._path = Path(path)
        # Each of these four mirrors exactly one of scraper.py's old
        # module-level dicts -- same keying: (symbol, timeframe, direction)
        # -> {price_key: value}. Kept as separate top-level dicts (not
        # merged into one nested structure) so a corrupt/missing file only
        # ever costs a clean-slate restart, same blast radius as before
        # this store existed at all.
        self._last_seen: dict[str, dict[int, int]] = {}
        self._missing_streak: dict[str, dict[int, int]] = {}
        self._pending_retest: dict[str, dict[int, int]] = {}
        self._pending_formed: dict[str, dict[int, int]] = {}
        # 2-consecutive-poll confirmation gate for scraper.py's
        # pine_retested_flag fallback (added 2026-08-28, same shape/reason
        # as pending_retest/pending_formed -- see _apply_direction's own
        # comment at that branch for the slot-reuse false-positive it
        # guards against). Missing from an older state file just means
        # "nothing was pending" -- same safe default every other dict here
        # already gets via .get(section, {}) below.
        self._pending_pine_confirm: dict[str, dict[int, int]] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str, direction: str) -> str:
        return f"{symbol}|{timeframe}|{direction}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for attr in ("last_seen", "missing_streak", "pending_retest", "pending_formed", "pending_pine_confirm"):
            section = raw.get(attr, {})
            setattr(self, f"_{attr}", {
                key: {int(pk): int(v) for pk, v in inner.items()}
                for key, inner in section.items()
            })

    def _save(self) -> None:
        out = {
            "last_seen": self._last_seen,
            "missing_streak": self._missing_streak,
            "pending_retest": self._pending_retest,
            "pending_formed": self._pending_formed,
            "pending_pine_confirm": self._pending_pine_confirm,
        }
        self._path.write_text(json.dumps(out))

    def get_last_seen(self, symbol: str, timeframe: str, direction: str) -> dict[int, int]:
        return dict(self._last_seen.get(self._key(symbol, timeframe, direction), {}))

    def get_missing_streak(self, symbol: str, timeframe: str, direction: str) -> dict[int, int]:
        return dict(self._missing_streak.get(self._key(symbol, timeframe, direction), {}))

    def get_pending_retest(self, symbol: str, timeframe: str, direction: str) -> dict[int, int]:
        return dict(self._pending_retest.get(self._key(symbol, timeframe, direction), {}))

    def get_pending_formed(self, symbol: str, timeframe: str, direction: str) -> dict[int, int]:
        return dict(self._pending_formed.get(self._key(symbol, timeframe, direction), {}))

    def get_pending_pine_confirm(self, symbol: str, timeframe: str, direction: str) -> dict[int, int]:
        return dict(self._pending_pine_confirm.get(self._key(symbol, timeframe, direction), {}))

    def update(self, symbol: str, timeframe: str, direction: str,
               last_seen: dict[int, int], missing_streak: dict[int, int],
               pending_retest: dict[int, int], pending_formed: dict[int, int],
               pending_pine_confirm: dict[int, int]) -> None:
        """Writes all five values for this (symbol, timeframe, direction)
        in one call and saves once -- matches exactly how
        run_once_pane() already computes all five together per direction
        per poll, so this never leaves them out of sync with each
        other on disk."""
        key = self._key(symbol, timeframe, direction)
        self._last_seen[key] = last_seen
        self._missing_streak[key] = missing_streak
        self._pending_retest[key] = pending_retest
        self._pending_formed[key] = pending_formed
        self._pending_pine_confirm[key] = pending_pine_confirm
        self._save()
