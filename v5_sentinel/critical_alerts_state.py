"""Persisted per-level alert dedup for the critical-alerts bot.
2026-09-07, new design replacing the old milestone/position tracking
(profit_alerts_state.py, retired): "one alert per valid level... m30
shouldn't send a new alert unless it has a new value... after a couple
of hours m30 support is still valid, but has a new price value, then it
becomes valid again, as the level is not the same."

Keyed by (timeframe_minutes, line_no) -- NOT by role (support/
resistance), since line_no is the stable identity (the fast or slow
trail line) even as its role flips between support and resistance as
price crosses it; role can be recovered from the level itself at alert
time. Deliberately independent of Reversal Manager's own
LevelEligibilityStore ("traded" state) -- alerts fire on every touch of
a genuinely new value regardless of whether RM has already traded that
level, they are not the same concept.

DEDUP KEY FIXED 2026-09-07 (found live, same day -- "i received same
alert twice, once per bar is fine"): originally deduped on the level's
own float VALUE (within a small epsilon), but that value comes from the
same path-dependent copy_rates recompute already documented elsewhere
in this project as capable of tiny cycle-to-cycle jitter -- occasionally
enough to exceed the epsilon and look like "a new level" when the
underlying bar hadn't actually changed. Deduping on the timeframe's own
last CLOSED BAR TIME instead (HTFState.last_time) is far more robust:
that's a discrete integer that only changes when a real new bar closes,
immune to float noise, and it naturally satisfies the original "new
value = alert again" requirement too, since a level's value only ever
actually changes on a bar close in the first place. "Once per bar" is
the confirmed, simpler contract now -- even a bar that closes with the
SAME value as before gets its own fresh alert if price touches it again,
which the user confirmed is fine.
"""
from __future__ import annotations

import json
from pathlib import Path


class CriticalAlertState:
    def __init__(self, path: str):
        self._path = Path(path)
        self._alerted: dict[str, int] = {}  # "{tf}:{line_no}" -> last alerted bar_time
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._alerted = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            self._alerted = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._alerted))

    @staticmethod
    def _key(tf_minutes: int, line_no: int) -> str:
        return f"{tf_minutes}:{line_no}"

    def already_alerted(self, tf_minutes: int, line_no: int, bar_time: int) -> bool:
        """True if this timeframe+line slot was already alerted for THIS
        exact closed bar -- immune to float jitter in the level's own
        value, since bar_time only changes on a genuine new bar close."""
        return self._alerted.get(self._key(tf_minutes, line_no)) == bar_time

    def mark_alerted(self, tf_minutes: int, line_no: int, bar_time: int) -> None:
        self._alerted[self._key(tf_minutes, line_no)] = bar_time
        self._save()
