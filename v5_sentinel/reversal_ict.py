"""RM-ICT -- Reversal Manager's SECOND component. Confirmed with the
user 2026-09-09: two entry methods planned in total; this module builds
the FIRST only ("ATR flip") -- a candle-identification-based second
method is explicitly deferred to later, per the user's own words ("first
one is via atr flip, second one is via candle identification strategy
(we will build later)").

ATR FLIP entry rule (user's own words, 2026-09-09): "whenever price
enters into zone, either bullish or bearish ob zone, we need a flip from
3f opposite side... if a price enters bullish ob, from the time price
enter into zone, we keep a watch and we have a flip on m3 bullish flip
we enter." So:

  - "price enters the zone" is exactly the NLB/NSB Block's own live
    RETEST signal (nlb_nsb_block.py) -- a bullish OB (NSB) retested
    arms a watch for a BULLISH M3 flip -> BUY; a bearish OB (NLB)
    retested arms a watch for a BEARISH M3 flip -> SELL. No separate
    touch-tracking needed here at all -- the Block already IS that live
    tick-driven watch, built for exactly this purpose.
  - Only a genuine FLIP counts (never TRAP_RESOLVED) -- same scope as
    the sibling STR component's own "3F" trigger (reversal_entry.py),
    for the same reason: a trap resolving back to its prior direction
    was never treated as an edge/trigger anywhere else in this project
    either.
  - Bar-close-gated, via the SAME BridgeBarFlipTracker Trend Manager and
    RM's own STR component already run on M3 -- never a live-tick
    trigger.

SL: "sl as per m3 far line with buffer as it is" -- identical basis to
STR's own "3F" SL: M3's own far trail line +/- cfg.sl_buffer, FROZEN at
the exact bar that produced the flip (BridgeBarFlipTracker.
event_far_near()), not a live re-read. Reuses the very same tracker
instance and sl_buffer value the STR component already uses -- "as it
is" means no new/different buffer for this component.

ELIGIBILITY ("traded" tracking): kept in THIS module's OWN separate
state file (ICTEligibilityStore below), never written back into the
shared NLB/NSB Block itself. The Block is owned and written EXCLUSIVELY
by nlb_nsb_watcher.py, a completely separate process publishing fresh
retest/invalidation state on its own cycle -- if this component wrote
"traded" flags into that same file too, the two processes' writes could
race and silently lose one or the other's update. A zone fires AT MOST
ONCE in its lifetime here: once traded, it's excluded from every future
scan for good. This deliberately differs from STR's own HTF levels
(which reset eligibility whenever their parent timeframe's character
changes again) -- an OB zone has no such changing "character" of its
own; it's either still a valid, live level, or it's been invalidated and
the Block has already deleted it outright, which excludes it from
scanning automatically with nothing extra to track.

Position lifecycle: identical to STR's own (reversal_main.py's
_process_signal, now generalized to accept either component) -- both
share Reversal Manager's SAME magic number and SAME one-position-at-a-
time slot, so an ICT signal runs through the exact same no-position /
opposite-direction-square-off-and-reopen / same-direction-already-open /
partially-cut-refresh branching STR already uses. User's own words:
"any leftovers, or whatever condition of trade, square off on opposite
side valid setup and fire opposite side" -- exactly that shared
lifecycle, nothing new to build for it. Credited under its own
"V5S-RM-ICT-{tag}" comment prefix so it's visually distinguishable from
an STR-sourced position at a glance (see reversal_main.py's own
docstring for the prefix history).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.flip_state import EventType
from v5_sentinel.nlb_nsb_block import BlockStore

_M3_MINUTES = 3


@dataclass(frozen=True)
class ICTSignal:
    direction: int              # 1 buy, -1 sell
    zone_id: str
    timeframe_name: str          # "H1"
    zone_top: float
    zone_btm: float
    trigger: str                  # always "ATR_FLIP" for this entry method
    sl: float


class ICTEligibilityStore:
    """Persists which OB zones (by their own stable Block zone_id) this
    component has already traded -- see module docstring for why this
    is a SEPARATE file from the Block's own, not written back into it."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._traded: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._traded = set(json.loads(self._path.read_text()))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._traded = set()

    def _save(self) -> None:
        self._path.write_text(json.dumps(sorted(self._traded)))

    def is_traded(self, zone_id: str) -> bool:
        return zone_id in self._traded

    def mark_traded(self, zone_id: str) -> None:
        if zone_id not in self._traded:
            self._traded.add(zone_id)
            self._save()


def find_ict_signals(
    symbol: str,
    block_state_file: str,
    eligibility: ICTEligibilityStore,
    tracker: BridgeBarFlipTracker,
    sl_buffer: float,
) -> list[ICTSignal]:
    """Every currently-retested, untraded OB zone whose implied
    direction matches M3's own fresh, bar-close-confirmed FLIP. Reads
    the Block fresh (read-only -- this component never writes to it)
    every call, same pattern main.py's own ICT Guard already uses."""
    fs_m3 = tracker.update(symbol, _M3_MINUTES)
    if fs_m3 is None or not fs_m3.event_just_happened() or fs_m3.last_event is None:
        return []
    if fs_m3.last_event.event_type != EventType.FLIP:
        return []
    m3_flip_dir = fs_m3.last_event.confirmed.value

    frozen = tracker.event_far_near(_M3_MINUTES)
    if frozen is None:
        return []  # shouldn't happen once a FLIP has fired, but no basis to guess from
    far, _near = frozen
    sl = far - sl_buffer if m3_flip_dir == 1 else far + sl_buffer

    # Bullish OB (NSB) retested -> watching for a BULLISH M3 flip -> BUY.
    # Bearish OB (NLB) retested -> watching for a BEARISH M3 flip -> SELL.
    target_role = "no_short_buffer" if m3_flip_dir == 1 else "no_long_buffer"

    store = BlockStore(block_state_file)
    signals: list[ICTSignal] = []
    for zone in store.zones():
        if zone.role != target_role or not zone.retested:
            continue
        if eligibility.is_traded(zone.zone_id):
            continue
        signals.append(ICTSignal(
            direction=m3_flip_dir, zone_id=zone.zone_id, timeframe_name=zone.timeframe_name,
            zone_top=zone.top, zone_btm=zone.btm, trigger="ATR_FLIP", sl=sl,
        ))
    return signals
