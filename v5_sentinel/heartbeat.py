"""Per-process heartbeat file -- each V5-Sentinel bot writes its own after
every main-loop iteration (success OR a caught, logged cycle error --
either way proves the LOOP ITSELF is still alive and iterating), so
watchdog.py can tell "genuinely cycling" apart from "OS process still
exists but is silently stuck."

Added 2026-09-08, found live: BOTH Trend Manager and Reversal Manager
independently went completely inert for extended periods (Reversal
Manager for over 25 HOURS straight) while still showing as running
processes in the OS process list -- nothing existing distinguished that
from healthy. No captured log survived either incident to show what
went wrong at the moment each froze, so this doesn't explain the root
cause -- it only makes the SYMPTOM impossible to miss going forward,
which is the actionable part: a human (or an alert) needs to know
within minutes, not stumble onto it by chance a day later while
investigating something else.

Deliberately separate from any bot's own state files (runtime state,
SL state, level eligibility, etc.) -- those only update when something
notable actually happens, which can legitimately go a long time between
writes even on a perfectly healthy bot (e.g. TM's runtime_state.json
only changes on a genuine M3 event). A heartbeat has exactly one job:
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
