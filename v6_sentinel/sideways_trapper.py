"""Sideways Trapper -- RM-STR's own guard against the "M5 chop" pattern:
confirmed live 2026-09-22 (user's own words), three M5-triggered RM-STR
trades fired 13:57-14:12 IST while H1/M30 were flat/undecided, all three
stopped out within minutes, -$123.45 total. The higher timeframes weren't
breaking anything -- M5's own line just kept re-arming and re-firing inside
the noise.

RULE (user's own words): "if a buy sl hit, record the qualified entry price
for buy... next qualifying buy should be minimum [N] points above or below
the qualified price of previous buy entry." Tracked independently per
direction (a SELL's own history never blocks a BUY and vice versa), and
ACROSS ALL of RM-STR's 8 timeframes -- not per-timeframe -- since the whole
point is "don't re-enter right where this direction just got stopped out,"
regardless of which HTF line triggers the next attempt.

Recording happens ONLY on a genuine SL_HIT close (never a manual close,
square-off, Exit Manager close, or a win) -- reversal_main.py feeds this
from trade_journal.TradeJournal.reconcile()'s own return value, which is
the single source of truth for "was this actually an SL hit."

RESET (user, 2026-09-22, simplified after discussion -- CISD does NOT reset
it, only structure does): the recorded price for a direction is cleared the
moment M15's own ATR-dual CONFIRMED structure genuinely FLIPS since it was
recorded (a trap/watching state is not a flip, same convention as every
other structure read in this project) -- the reasoning being that a real
M15 flip means the broader context has actually changed, so the old
"don't re-enter here" price no longer applies. No time-based expiry -- a
flip is the only way out (confirmed explicitly: "not even m15 cisd can
reset it").

No fallback: if the current M15 structure can't be read (bridge/tracker has
nothing yet), the existing block just stays in effect unchanged -- never
guessed away.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class SidewaysTrapper:
    def __init__(self, path: str):
        self._path = Path(path)
        # "1" / "-1" -> {"price": float, "m15_structure": int}
        self._state: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._state = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._state = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._state))

    def record_sl_hit(self, direction: int, entry_price: float, m15_structure: Optional[int]) -> None:
        """Call once, right when a trade in `direction` is confirmed SL-hit --
        overwrites whatever was recorded for that direction before."""
        self._state[str(direction)] = {"price": entry_price, "m15_structure": m15_structure}
        self._save()

    def blocks(self, direction: int, entry_price: float, min_distance: float,
              m15_structure: Optional[int]) -> bool:
        """True if a qualifying trade in `direction` at `entry_price` should be
        SKIPPED this cycle -- no prior SL-hit recorded for this direction, or
        the recorded one has since been cleared by a genuine M15 structure
        flip, always returns False (not blocked)."""
        entry = self._state.get(str(direction))
        if entry is None:
            return False
        recorded_structure = entry.get("m15_structure")
        if (m15_structure is not None and recorded_structure is not None
                and m15_structure != recorded_structure):
            del self._state[str(direction)]   # a genuine M15 flip since this was recorded -- clear it
            self._save()
            return False
        return abs(entry_price - entry["price"]) < min_distance
