"""Per-process heartbeat file -- each V7-Sentinel bot writes its own
after every main-loop iteration (success OR a caught, logged cycle error
-- either way proves the LOOP ITSELF is still alive and iterating), so a
watchdog can tell "genuinely cycling" apart from "OS process still
exists but is silently stuck." Ported verbatim from
v5_sentinel/heartbeat.py (2026-09-18) -- takes `path` as an explicit
argument already, so per-symbol heartbeat files (via
config.state_file_for()) need no change here.

Deliberately separate from any bot's own state files (runtime state, SL
state, level eligibility, etc.) -- those only update when something
notable actually happens, which can legitimately go a long time between
writes even on a perfectly healthy bot. A heartbeat has exactly one job:
prove the loop turned over, regardless of whether that cycle did
anything interesting.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def write(path: str) -> None:
    """Call once per main-loop iteration, unconditionally -- after the
    try/except around run_once(), not inside it, so a caught per-cycle
    exception still counts as "the loop is alive," only an actual process
    death (or a genuine hang inside run_once itself) stops this from
    being written."""
    Path(path).write_text(json.dumps({"updated": time.time()}))


def read_age(path: str) -> Optional[float]:
    """Seconds since this heartbeat was last written, or None if the file
    doesn't exist yet (bot never started) or is unreadable (mid-write
    race, corrupt) -- callers should treat None the same as "very stale,"
    not as "healthy"."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
        return time.time() - float(raw["updated"])
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError):
        return None
