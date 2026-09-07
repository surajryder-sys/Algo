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

A level's value only ever changes when its OWN timeframe's trail line
actually moves (a new bar closing, or the ratchet catching up) -- not on
every cycle -- so this naturally alerts once per distinct level value,
any number of touches on the SAME value produce no further alerts, and a
genuinely new value (even on the exact same timeframe+line slot) always
gets its own fresh alert.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class CriticalAlertState:
    def __init__(self, path: str, value_epsilon: float = 0.05):
        self._path = Path(path)
        self._value_epsilon = value_epsilon
        self._alerted: dict[str, float] = {}  # "{tf}:{line_no}" -> last alerted value
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

    def already_alerted(self, tf_minutes: int, line_no: int, value: float) -> bool:
        """True if THIS same value (within value_epsilon, to absorb
        float noise -- not a genuine level move) was already alerted for
        this timeframe+line slot."""
        prior = self._alerted.get(self._key(tf_minutes, line_no))
        return prior is not None and abs(prior - value) <= self._value_epsilon

    def mark_alerted(self, tf_minutes: int, line_no: int, value: float) -> None:
        self._alerted[self._key(tf_minutes, line_no)] = value
        self._save()
