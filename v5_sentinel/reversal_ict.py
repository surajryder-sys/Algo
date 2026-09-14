"""RM-ICT -- Reversal Manager's SECOND component. Confirmed with the
user 2026-09-09: two entry methods planned in total; this module builds
the FIRST only ("ATR flip", extended 2026-09-12 to also include the
M1-Supertrend trigger, see below) -- a candle-identification-based
second method is explicitly deferred to later, per the user's own words
("first one is via atr flip, second one is via candle identification
strategy (we will build later)").

ENTRY RULE (user's own words, 2026-09-09, extended 2026-09-12): "whenever
price enters into zone, either bullish or bearish ob zone, we need a
flip from 3f opposite side... if a price enters bullish ob, from the
time price enter into zone, we keep a watch and we have a flip on m3
bullish flip we enter" -- and 2026-09-12: "a flip on m1 by supertrend,
or a flip on m3 which crosses both lines will make eligible to enter
into the trade... any one condition satisfying will take an entry."
So, TWO independent triggers now, either sufficient alone:

  - "price enters the zone" is exactly the NLB/NSB Block's own live
    RETEST signal (nlb_nsb_block.py) -- a bullish OB (NSB) retested
    arms a watch for a matching-direction flip; a bearish OB (NLB)
    retested arms a watch for the opposite. No separate touch-tracking
    needed here at all -- the Block already IS that live tick-driven
    watch, built for exactly this purpose.
  - "3F" -- M3's own ATR dual-trail FLIP (crosses BOTH lines), bar-close-
    gated via the SAME BridgeBarFlipTracker Trend Manager and RM's own
    STR component already run on M3. Only a genuine FLIP counts, never
    TRAP_RESOLVED -- same scope as the sibling STR component's own "3F".
  - "ST1F" -- M1's own Supertrend flip (st_bridge.fresh_flip()), bar-
    close-gated the same way on the MQL5 side already (see st_bridge.py).
    Labeled "ST1F" (not bare "1F"), 2026-09-12: "1F Supertrend flip
    comment should be, ST1F" -- and M1 is scoped to Reversal Manager
    only, "no where m1 plays any role apart from reversal managers"
    (Trend Manager's own structure signals never touch M1).
  Whichever fires first (or both, on the rare cycle they coincide)
  produces a signal for any retested zone matching its direction.

SL: "3F" -> "sl as per m3 far line with buffer as it is" -- M3's own far
trail line +/- cfg.sl_buffer, FROZEN at the exact bar that produced the
flip (BridgeBarFlipTracker.event_far_near()), not a live re-read.
"ST1F" -> M1's own Supertrend line value +/- cfg.sl_buffer, read
directly off that fresh-flip bar (the MQL5 side already only updates it
once per closed bar, so no separate freezing mechanism is needed).
Reuses the very same sl_buffer value STR uses -- "as it is" means no
new/different buffer for this component.

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

Exact zone_id matching alone isn't enough, though (2026-09-15, found
live): the SAME real M15 price level fired an entry twice in one day,
~16 hours apart, because tv_scraper's own detection had churned the
real zone out of its own visible list and back in between the two
firings, minting it a brand-new start_time/identity the second time --
never the same zone_id as the one already traded, so is_traded() alone
never caught it. ICTEligibilityStore.overlaps_traded() is the geometric
backstop: a new zone is also rejected if its own range substantially
overlaps one already traded today, regardless of identity.

Position lifecycle: identical to STR's own (reversal_main.py's
_process_signal, generalized to accept either component). No position
-> open fresh. Opposite direction -> square off + reopen. SAME
direction, whether still full-size or already partially cut -> NO-OP,
just mark this flip traded -- 2026-09-12, user's own direction: "no
closing leftover and entering full qty again... we not closing leftovers
and entering fresh trade." The old "already partially cut -> refresh
(close leftover + reopen full)" branch is retired; a same-direction
match now behaves identically whether the position is still full-size or
already cut down. Credited under its own "V5S-RM-ICT-{tag}" comment
prefix so it's visually distinguishable from an STR-sourced position at
a glance (see reversal_main.py's own docstring for the prefix history).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from v5_sentinel import st_bridge
from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.flip_state import EventType
from v5_sentinel.nlb_nsb_block import BlockStore

_M1_MINUTES = 1
_M3_MINUTES = 3


@dataclass(frozen=True)
class ICTSignal:
    direction: int              # 1 buy, -1 sell
    zone_id: str
    timeframe_name: str          # "H1"
    zone_top: float
    zone_btm: float
    trigger: str                  # "3F" or "ST1F" -- same labels STR's own triggers use (2026-09-09, user's own direction: "dont use ATR flip on comment, You can use 3F"); the "V5S-RM-{STR|ICT}-" component prefix already tells the two components apart, so reusing STR's labels here isn't ambiguous.
    sl: float


class ICTEligibilityStore:
    """Persists which OB zones (by their own stable Block zone_id) this
    component has already traded -- see module docstring for why this
    is a SEPARATE file from the Block's own, not written back into it.

    ALSO persists each traded zone's own (top, btm) range (2026-09-15,
    found live: the exact same real M15 price level [4292.110-4327.490]
    fired an RM-ICT trade TWICE in one day, ~16 hours apart -- not a
    dedup failure by zone_id, the two firings genuinely had different
    zone_ids, because tv_scraper's own detection had churned the real
    zone out of its own visible list and back in between the two,
    minting it a brand-new start_time/identity the second time -- same
    debounce-churn mechanism as the fabricated-zone incidents, just
    reproducing a REAL level's identity instead of inventing one).
    is_traded() alone can't catch this -- it only ever compares exact
    zone_ids. overlaps_traded() below is the geometric backstop: reject
    a new zone outright if its own range substantially overlaps a zone
    already traded today, regardless of whether the two share an
    identity. Zones persisted before this field existed carry None for
    their own range (no historical top/btm was ever recorded for them)
    -- they simply never gain overlap protection retroactively, which is
    fine; this store isn't pruned at all, so old entries stay forever
    either way."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._traded: dict[str, Optional[tuple]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            if isinstance(raw, list):
                # Pre-2026-09-15 format: a bare list of zone_id strings,
                # no range ever recorded.
                self._traded = {zid: None for zid in raw}
            else:
                self._traded = {zid: (tuple(v) if v is not None else None) for zid, v in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._traded = {}

    def _save(self) -> None:
        payload = {zid: (list(rng) if rng is not None else None) for zid, rng in self._traded.items()}
        self._path.write_text(json.dumps(payload))

    def is_traded(self, zone_id: str) -> bool:
        return zone_id in self._traded

    def mark_traded(self, zone_id: str, top: Optional[float] = None, btm: Optional[float] = None) -> None:
        if zone_id not in self._traded:
            self._traded[zone_id] = (top, btm) if top is not None and btm is not None else None
            self._save()

    def overlaps_traded(self, top: float, btm: float, min_overlap_fraction: float = 0.6) -> Optional[str]:
        """Returns the zone_id of an already-traded zone whose own range
        overlaps [btm, top] by at least min_overlap_fraction of the
        SMALLER of the two ranges, or None if nothing overlaps that
        much. Fractional (not exact-match) deliberately -- a re-detected
        copy of the same real level isn't guaranteed to read pixel-
        identical top/btm on rediscovery, just substantially the same
        range."""
        for zone_id, rng in self._traded.items():
            if rng is None:
                continue
            traded_top, traded_btm = rng
            overlap = min(top, traded_top) - max(btm, traded_btm)
            if overlap <= 0:
                continue
            smaller_range = min(top - btm, traded_top - traded_btm)
            if smaller_range <= 0:
                continue
            if overlap / smaller_range >= min_overlap_fraction:
                return zone_id
        return None


def _scan_matching_zones(store: BlockStore, eligibility: ICTEligibilityStore, direction: int, trigger: str,
                         sl: float) -> list[ICTSignal]:
    """Every currently-retested, untraded OB zone whose implied direction
    matches `direction`, tagged with whichever trigger just qualified
    it. Bullish OB (NSB) retested -> matches a BUY; bearish OB (NLB)
    retested -> matches a SELL."""
    target_role = "no_short_buffer" if direction == 1 else "no_long_buffer"
    signals: list[ICTSignal] = []
    for zone in store.zones():
        if zone.role != target_role or not zone.retested:
            continue
        if eligibility.is_traded(zone.zone_id):
            continue
        dup = eligibility.overlaps_traded(zone.top, zone.btm)
        if dup is not None:
            print(f"[V5S-ICT] zone {zone.zone_id} [{zone.btm:.3f}-{zone.top:.3f}] skipped -- "
                  f"substantially overlaps already-traded zone {dup}")
            continue
        signals.append(ICTSignal(
            direction=direction, zone_id=zone.zone_id, timeframe_name=zone.timeframe_name,
            zone_top=zone.top, zone_btm=zone.btm, trigger=trigger, sl=sl,
        ))
    return signals


def find_ict_signals(
    symbol: str,
    block_state_file: str,
    eligibility: ICTEligibilityStore,
    tracker: BridgeBarFlipTracker,
    sl_buffer: float,
) -> list[ICTSignal]:
    """Every currently-retested, untraded OB zone whose implied direction
    matches EITHER M3's own fresh, bar-close-confirmed ATR flip ("3F") or
    M1's own fresh Supertrend flip ("ST1F") -- either alone is sufficient
    (2026-09-12). Reads the Block fresh (read-only -- this component
    never writes to it) every call, same pattern main.py's own ICT Guard
    already uses."""
    store = BlockStore(block_state_file)
    signals: list[ICTSignal] = []

    fs_m3 = tracker.update(symbol, _M3_MINUTES)
    if (fs_m3 is not None and fs_m3.event_just_happened() and fs_m3.last_event is not None
            and fs_m3.last_event.event_type == EventType.FLIP):
        m3_flip_dir = fs_m3.last_event.confirmed.value
        frozen = tracker.event_far_near(_M3_MINUTES)
        if frozen is not None:  # shouldn't be None once a FLIP has fired, but no basis to guess from
            far, _near = frozen
            sl = far - sl_buffer if m3_flip_dir == 1 else far + sl_buffer
            signals.extend(_scan_matching_zones(store, eligibility, m3_flip_dir, "3F", sl))

    m1 = st_bridge.fresh_flip(symbol, _M1_MINUTES)
    if m1 is not None:
        sl = m1.supertrend - sl_buffer if m1.trend == 1 else m1.supertrend + sl_buffer
        signals.extend(_scan_matching_zones(store, eligibility, m1.trend, "ST1F", sl))

    return signals
