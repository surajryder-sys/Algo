"""TM-STR's M15 entry gate -- REDESIGNED 2026-09-22 (user: "m15 parenting
change, now its not recency, its structure and cisd"), replacing the earlier
"whichever event is most recent" bias entirely.

TWO independent M15 signals, READ TOGETHER (never one overriding the other by
recency any more):

  STRUCTURE -- the M15 ATR-dual CONFIRMED direction (bridge_bar_flip.
  BridgeBarFlipTracker, same bridge-sourced tracker as before): 1 = strong/up,
  -1 = weak/down. A trap (price between the two lines) is a non-event -- the
  confirmed direction just stays wherever the last genuine FLIP/TRAP_RESOLVED
  left it, exactly as before.

  CISD -- the M15 STANDING CISD (cisd_bridge.read_cisd(), not the momentary
  fresh_cisd()): 1 = bullish, -1 = bearish.

THE GATE (user's own 4 cases, exhaustive over the 2x2 combinations):
  - structure and CISD AGREE (both up or both down) -> only an M5 CISD in
    THAT SAME direction may fire; the opposite M5 CISD does nothing.
  - structure and CISD DISAGREE (conflict) -> BOTH directions are allowed;
    an M5 CISD fires a BUY or a SELL purely on its own direction, M15 raises
    no objection either way. The reasoning (not the user's stated words, my
    own inference): a disagreement means M15 itself is undecided/mid-
    transition, so M5 is left to decide freely rather than being gated by a
    stance M15 doesn't actually hold with conviction.

No fallback, no guess (same convention as every other bridge-sourced signal
in this project): if EITHER structure or CISD is unavailable (ATR bridge/
tracker has nothing yet, or the CISD bridge is stale/missing), there is NO
gate at all -- no entries, in either direction, until both are available
again.

WHAT THIS NO LONGER DOES: there is no single "bias direction" for an open
trade to be checked against any more, so TM-STR's own M15-bias-flip close is
GONE (user: "we have added m5 cisd exit so we can remove m15 bias exit
logic" -- the M5 flip exit and square-off, both already built, are now the
only TM-STR-internal ways a trade closes, alongside the SL and the separate
Exit Manager process).

Supertrend is deliberately NOT part of this (confirmed: "we see supertrend
later").

COLD START: the tracker only accumulates history once it is running; a
brand-new one bootstraps "confirmed since NOW" -- seed the tracker's state
file once from a live source before the first real run, same as always.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from v6_sentinel import cisd_bridge
from v6_sentinel.bridge_bar_flip import BridgeBarFlipTracker


@dataclass(frozen=True)
class M15Gate:
    allowed: frozenset[int]       # {1} buy-only, {-1} sell-only, or {1, -1} both allowed
    structure: int                 # 1 strong/up, -1 weak/down -- the M15 ATR-dual confirmed direction
    structure_since: int            # confirmed_since_time (or the last flip's bar_time) -- for logging
    cisd: int                        # 1 bullish, -1 bearish -- the M15 standing CISD direction
    cisd_time: int                    # that CISD's last_cisd_time -- for logging


def compute_gate(tracker: BridgeBarFlipTracker, symbol: str, tf_minutes: int) -> Optional[M15Gate]:
    """None if EITHER the ATR side or the standing CISD has nothing to offer
    right now -- no partial gate, no guess. `tracker` must be this symbol's
    own BridgeBarFlipTracker instance."""
    fs = tracker.update(symbol, tf_minutes)
    if fs is None:
        return None
    cisd = cisd_bridge.read_cisd(symbol, tf_minutes)
    if cisd is None:
        return None

    structure = fs.confirmed.value
    structure_since = fs.last_event.bar_time if fs.last_event is not None else fs.confirmed_since_time
    cisd_direction = cisd_bridge.direction_of(cisd)

    allowed = frozenset({structure}) if structure == cisd_direction else frozenset({1, -1})
    return M15Gate(allowed=allowed, structure=structure, structure_since=structure_since,
                  cisd=cisd_direction, cisd_time=cisd.last_cisd_time)
