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

WIDENED TO ANY RED CLOSE (2026-09-23, user: "dont just see trades which hit
sl / also see trades which closed in red, which is closed via exit manager
as well... its always entry level... from there +/- 5 points"): recording
now happens on ANY close that nets a LOSS (profit_net < 0), regardless of
exit_reason -- SL_HIT, CANDLEEXIT-*, LTFEXIT, BIASEXIT, SQOFF-in-red, all
of it -- not just a literal SL_HIT. The reference price recorded is always
the trade's own ENTRY price (never the exit/SL price), same as before.
A REAL side effect of this change (not just an addition): a profitable
SL_HIT (SL had trailed into profit before getting hit) no longer records
anything -- only a genuine loss does, matching "don't re-enter near where
we just LOST money," not "don't re-enter near any SL hit regardless of
outcome." trend_main.py/reversal_main.py feed this from trade_journal.
TradeJournal.reconcile()'s own return value (entry_price + profit_net),
the single source of truth for both.

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

_DIR_LABEL = {1: "BUY", -1: "SELL"}


class SidewaysTrapper:
    def __init__(self, path: str):
        self._path = Path(path)
        # "1" / "-1" -> {"price": float, "m15_structure": int}
        self._state: dict[str, dict] = {}
        self._load()
        # Set by blocks() whenever it actually blocks a signal this cycle, for the caller to
        # surface (print/decision_log/Telegram) once per outer cycle -- added 2026-09-24, user:
        # "can i know the reason of skipped trades... extend same alert to telegram." blocks()
        # itself has no cfg/telegram access (stays a small, dependency-free state object), so it
        # just records WHY here; trend_main.py/reversal_main.py read and clear it after calling
        # find_signal()/find_signals(). Overwritten on every blocking call -- if several
        # candidates block in the same cycle, only the last one is reported (same "collapse to
        # one alert per cycle, not one per candidate" convention reversal_main.py's own
        # redundant-signal alert already uses for the identical spam risk).
        self.last_block_reason: Optional[str] = None

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._state = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._state = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._state))

    def record_loss(self, direction: int, entry_price: float, m15_structure: Optional[int]) -> None:
        """Call once, right when a trade in `direction` closes at a net loss --
        any exit_reason, not just SL_HIT (see module docstring's own WIDENED
        TO ANY RED CLOSE section) -- overwrites whatever was recorded for
        that direction before."""
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
        distance = abs(entry_price - entry["price"])
        blocked = distance < min_distance
        if blocked:
            self.last_block_reason = (
                f"{_DIR_LABEL[direction]} @ {entry_price:.3f} is only {distance:.1f} points from the last "
                f"recorded loss @ {entry['price']:.3f} -- needs {min_distance:.1f}+ points")
        return blocked
