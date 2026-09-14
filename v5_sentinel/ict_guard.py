"""Shared ICT Guard -- the NLB/NSB Block proximity safeguard used by
TM-STR (main.py), RM-STR (reversal_main.py), and TM-ICT
(trend_manager_ict.py). Previously three near-identical copies of the
same check; consolidated here 2026-09-14 when the STICKY-blocking fix
below needed to land in all three at once.

STICKY BLOCKING (corrected 2026-09-14, found live -- a real trade sat
blocked for 56 straight cycles as its own gap to an NLB zone's edge
oscillated between ~3.0 and ~4.9pts, then fired the instant a single
noisy tick pushed the gap a few hundredths of a point past the 5.0pt
buffer). User's own words: "once a block is a block, it should never
allow when price slides down, thats wrong, similar for sell trade as
well." So: a zone that has EVER blocked an entry attempt (gap < buffer
at least once) becomes a STANDING block for that direction -- it never
un-blocks itself just because live price later drifts back outside the
buffer on its own. The live proximity check only ever DISCOVERS a zone
is dangerous; once discovered, that finding sticks.

The only way a sticky block clears is the zone itself disappearing from
the Block entirely -- nlb_nsb_block.py deletes a zone outright the
instant live price genuinely invalidates it (trades beyond its far
edge), at which point there's no zone left to check against, so
prune() below removes the now-meaningless sticky entry too. A zone
merely getting FARTHER away while still existing does NOT clear it --
only its own real invalidation does.

Each component keeps its OWN sticky-block memory (own state file), NOT
shared across TM-STR/RM-STR/TM-ICT -- matches this project's "own
state, own everything per component" convention; one component's entry
price getting blocked near a zone says nothing about whether another
component's own different entry price/timing would also be blocked.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from v5_sentinel import nlb_nsb_block


class ICTGuardStickyStore:
    """Persists which Block zone_ids have EVER blocked an entry attempt
    for this component, scoped implicitly to whichever role each caller
    checks (a zone only ever blocks ONE direction by its own role, so no
    separate per-direction key is needed)."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._blocked: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._blocked = set(json.loads(self._path.read_text()))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._blocked = set()

    def _save(self) -> None:
        self._path.write_text(json.dumps(sorted(self._blocked)))

    def is_blocked(self, zone_id: str) -> bool:
        return zone_id in self._blocked

    def mark_blocked(self, zone_id: str) -> None:
        if zone_id not in self._blocked:
            self._blocked.add(zone_id)
            self._save()

    def prune(self, existing_zone_ids: set) -> None:
        """Drops any sticky entry for a zone_id no longer present in the
        Block at all -- that zone has been genuinely invalidated and
        deleted (nlb_nsb_block.py never merely "shrinks" a zone), so
        there is nothing left for the sticky entry to mean."""
        stale = self._blocked - existing_zone_ids
        if stale:
            self._blocked -= stale
            self._save()


def check(block_state_file: str, sticky: ICTGuardStickyStore, direction: int, entry_price: float,
         buffer_points: float) -> Optional[str]:
    """Returns a human-readable block reason if blocked -- either a
    standing sticky block from an earlier cycle, or a fresh proximity
    breach discovered THIS cycle (which immediately becomes sticky
    too) -- else None. A LONG checks every NLB (bearish OB) zone's own
    BOTTOM edge; a SHORT checks every NSB (bullish OB) zone's own TOP
    edge. Also prunes `sticky` against the Block's current zone set on
    every call, so a genuinely invalidated zone's standing block clears
    itself automatically without any separate housekeeping step."""
    target_role = "no_long_buffer" if direction == 1 else "no_short_buffer"
    store = nlb_nsb_block.BlockStore(block_state_file)
    zones = store.zones()
    sticky.prune({z.zone_id for z in zones})

    for zone in zones:
        if zone.role != target_role:
            continue
        if sticky.is_blocked(zone.zone_id):
            return (f"{zone.timeframe_name} {zone.role} [{zone.btm:.3f}-{zone.top:.3f}] -- "
                   f"standing block (was too close at least once; doesn't clear just because "
                   f"price drifted back out)")
        edge = zone.btm if target_role == "no_long_buffer" else zone.top
        gap = abs(entry_price - edge)
        if gap < buffer_points:
            sticky.mark_blocked(zone.zone_id)
            return (f"{zone.timeframe_name} {zone.role} [{zone.btm:.3f}-{zone.top:.3f}] "
                   f"edge@{edge:.3f} is only {gap:.3f}pts away")
    return None
