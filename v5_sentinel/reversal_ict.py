"""RM-ICT -- Reversal Manager's SECOND component. Confirmed with the
user 2026-09-09: two entry methods planned in total; this module builds
the FIRST only -- a candle-identification-based second method is
explicitly deferred to later, per the user's own words ("first one is
via atr flip, second one is via candle identification strategy (we will
build later)").

ENTRY RULE -- REDESIGNED 2026-09-15 around the SAME M15 PRIMARY
STRUCTURE bias reversal_entry.py's own STR component now runs on (see
that module's own docstring for the full design -- this is the
identical model, just applied to OB zones instead of HTF levels), THEN
CORRECTED LATER THE SAME DAY once the retest requirement below was
found missing:

  A zone is a CANDIDATE once it EXISTS in the Block (any NLB/NSB zone
  matching the desired direction's role), has been RETESTED (live price
  has actually re-entered it -- zone.retested, nlb_nsb_watcher.py's own
  independently-computed touch flag), and hasn't been traded (or
  substantially overlapped one already traded, see
  ICTEligibilityStore.overlaps_traded()). The retest requirement was
  briefly dropped (2026-09-15, "we need to trade only on virgin
  (untested) zones... disqualifies if the zone gets invalidated") and
  reinstated the SAME DAY once it produced a real live trade: a SELL
  fired off a zone 11+ points from where price actually was, because
  nothing linked the M1 flip to that specific zone. "Virgin(untested)"
  turned out to mean "not yet TRADED" (an eligibility concept, still
  correctly enforced by is_traded/overlaps_traded below), not "price
  hasn't retested it" -- the retest requirement itself is a SEPARATE,
  necessary condition: "it's not about the flip at all, its about the
  retest and followed up with flip... thats how the exact reversal
  trade works." Mirrors reversal_entry.py's own scan_touches()/
  is_touched() gate on STR's HTF levels exactly, just keyed off the
  Block's own retested flag instead of a separate touch-arm store,
  since nlb_nsb_watcher.py already computes it independently. Only the
  Block's own live INVALIDATION disqualifies a zone outright (price
  trading beyond its far edge deletes it from the Block entirely, which
  naturally removes it from every future scan).

  PRIMARY STRUCTURE (M15) -- structure.compute_structure_signal(tracker,
  symbol, 15), the exact same recency-arbitrated engine reversal_entry.py
  and TM-STR's own M5 parent bias already run on.

  For a candidate zone wanting direction D (its own role):
    - If Primary Structure's CURRENT direction == D ("M15 agrees") ->
      the trigger is EITHER M1's own fresh Supertrend flip to D
      ("ST1F") or M3's own fresh ATR-dual FLIP to D ("M3F") -- either
      alone sufficient (corrected 2026-09-15, same day: "if primary
      structure agrees then it can enter on ST1F, or M3 Dual atr flip").
    - If Primary Structure's CURRENT direction != D, or is unavailable
      -> neither ST1F nor M3F fire; instead wait for EITHER M3's own
      fresh Supertrend flip to D ("ST3F") or M5's own fresh ATR-dual
      FLIP to D ("M5F") -- whichever happens first ("the special
      condition was only when primary structure doesn't agree" -- the
      race is specific to this tier, ST1F/M3F above are each
      independently sufficient, not racing each other). All four
      bar-close-gated, privileged triggers, same mechanism as
      reversal_entry.py's own.

  Bare "3F" (M3's own ATR dual-trail flip, unconditional, no M15 gate at
  all) is RETIRED as of this redesign -- user's own words: "removing 3F
  dual atr flip, as it might create a lot of noise" -- but survives
  scoped down to "M3F" above, firing only when M15 already agrees, which
  is also what addresses the original noise concern.

CISD TRIGGERS -- ADDED 2026-09-15, ALONGSIDE the four above, exactly
mirroring reversal_entry.py's own addition (see that module's own
docstring for the full confirmed design): M3 ("M3CD") and M5 ("M5CD")
only, sourced from cisd_bridge.py's reader of the ported AlgoAlpha CISD
indicator (mql5/CISD_AlgoAlpha.mq5). Deliberately NO M15 Primary
Structure gate ("no gate at all for CISD triggers") -- fires whenever a
fresh CISD confirms on its own timeframe, independent of Primary
Structure's current bias or even its availability, which is why these
two live OUTSIDE the `if primary is not None` block the four above sit
inside (see find_ict_signals() itself).

SL: "ST1F" -> M1's own Supertrend line value +/- cfg.sl_buffer, read
directly off the fresh-flip bar. "ST3F" -> M3's own Supertrend line
value +/- cfg.sl_buffer, same mechanism one timeframe up. "M3F"/"M5F" ->
that timeframe's own far ATR trail line, FROZEN at the exact bar that
produced the flip (BridgeBarFlipTracker.event_far_near()) +/-
cfg.sl_buffer -- same freezing idiom the original unconditional "3F"
used. "M3CD"/"M5CD" -> that timeframe's own NEAREST ACTIVE SWING low/
high (cisd_bridge.sl_basis(), frozen at confirmation, NOT
last_cisd_level -- see cisd_bridge.py's own SL BASIS docstring) +/-
cfg.sl_buffer. Reuses the very same sl_buffer value STR uses -- "as it
is" means no new/different buffer for
this component.

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

from v5_sentinel import cisd_bridge, st_bridge, structure
from v5_sentinel.bridge_bar_flip import BridgeBarFlipTracker
from v5_sentinel.flip_state import EventType
from v5_sentinel.nlb_nsb_block import BlockStore

_M1_MINUTES = 1
_M3_MINUTES = 3
_M5_MINUTES = 5
_M15_MINUTES = 15


@dataclass(frozen=True)
class ICTSignal:
    direction: int              # 1 buy, -1 sell
    zone_id: str
    timeframe_name: str          # "H1"
    zone_top: float
    zone_btm: float
    trigger: str                  # "ST1F" | "ST3F" | "M5F" | "M3CD" | "M5CD" -- same labels STR's own triggers use (2026-09-09, user's own direction: "dont use ATR flip on comment, You can use 3F"; 2026-09-15 the trigger SET itself changed but the shared-labels reasoning still holds); the "V5S-RM-{STR|ICT}-" component prefix already tells the two components apart, so reusing STR's labels here isn't ambiguous.
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
    """Every currently-EXISTING (not invalidated -- an invalidated zone
    is already deleted from the Block outright, so nothing extra to
    filter for that here), RETESTED, untraded OB zone whose implied
    direction matches `direction`, tagged with whichever trigger just
    qualified it. Bullish OB (NSB) -> matches a BUY; bearish OB (NLB) ->
    matches a SELL.

    RETEST REQUIREMENT REINSTATED 2026-09-15 (same day it was removed,
    found live): a real SELL fired off a zone 11+ points away from where
    price actually was -- ST1F only checks that an M1 Supertrend flip
    happened and SOME untested zone matching direction exists ANYWHERE
    in the Block, with no link between the two. User's own words: "it's
    not about the flip at all, its about the retest and followed up with
    flip!!!!! thats how the exact reversal trade works." The 2026-09-15
    redesign that dropped this check conflated two separate things: (1)
    traded-eligibility (don't re-trade the same zone -- this is what
    "virgin(untested) zone... disqualifies if the zone gets invalidated"
    actually meant, and stays fixed below via is_traded/
    overlaps_traded) and (2) retest gating (price must have actually
    reached the zone before a flip can trigger off it -- this is
    identical in spirit to reversal_entry.py's own scan_touches()/
    is_touched() requirement for STR's HTF levels, and should never have
    been dropped). zone.retested is nlb_nsb_watcher.py's own
    independently-computed live-tick touch flag (see nlb_nsb_block.py's
    own docstring) -- reusing it here rather than tracking a second,
    redundant touch detector."""
    target_role = "no_short_buffer" if direction == 1 else "no_long_buffer"
    signals: list[ICTSignal] = []
    for zone in store.zones():
        if zone.role != target_role:
            continue
        if not zone.retested:
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
    """Every currently-valid (not invalidated), retested, untraded OB
    zone whose implied direction matches one of the four M15-Primary-
    Structure-gated triggers this cycle (ST1F/M3F/ST3F/M5F), PLUS the
    two ungated CISD triggers (M3CD/M5CD) -- see module docstring for
    the full design, identical to reversal_entry.py's own STR component.
    Reads the Block fresh (read-only -- this component never writes to
    it) every call, same pattern main.py's own ICT Guard already uses."""
    store = BlockStore(block_state_file)
    signals: list[ICTSignal] = []

    primary = structure.compute_structure_signal(tracker, symbol, _M15_MINUTES)
    if primary is not None:
        m1 = st_bridge.fresh_flip(symbol, _M1_MINUTES)
        if m1 is not None and primary.direction == m1.trend:
            sl = m1.supertrend - sl_buffer if m1.trend == 1 else m1.supertrend + sl_buffer
            signals.extend(_scan_matching_zones(store, eligibility, m1.trend, "ST1F", sl))

        fs_m3_atr = tracker.update(symbol, _M3_MINUTES)
        if (fs_m3_atr is not None and fs_m3_atr.event_just_happened() and fs_m3_atr.last_event is not None
                and fs_m3_atr.last_event.event_type == EventType.FLIP):
            m3_atr_flip_dir = fs_m3_atr.last_event.confirmed.value
            if primary.direction == m3_atr_flip_dir:
                frozen_m3 = tracker.event_far_near(_M3_MINUTES)
                if frozen_m3 is not None:  # shouldn't be None once a FLIP has fired, but no basis to guess from
                    far_m3, _near_m3 = frozen_m3
                    sl = far_m3 - sl_buffer if m3_atr_flip_dir == 1 else far_m3 + sl_buffer
                    signals.extend(_scan_matching_zones(store, eligibility, m3_atr_flip_dir, "M3F", sl))

        m3 = st_bridge.fresh_flip(symbol, _M3_MINUTES)
        if m3 is not None and primary.direction != m3.trend:
            sl = m3.supertrend - sl_buffer if m3.trend == 1 else m3.supertrend + sl_buffer
            signals.extend(_scan_matching_zones(store, eligibility, m3.trend, "ST3F", sl))

        fs_m5 = tracker.update(symbol, _M5_MINUTES)
        if (fs_m5 is not None and fs_m5.event_just_happened() and fs_m5.last_event is not None
                and fs_m5.last_event.event_type == EventType.FLIP):
            m5_flip_dir = fs_m5.last_event.confirmed.value
            if primary.direction != m5_flip_dir:
                frozen = tracker.event_far_near(_M5_MINUTES)
                if frozen is not None:  # shouldn't be None once a FLIP has fired, but no basis to guess from
                    far, _near = frozen
                    sl = far - sl_buffer if m5_flip_dir == 1 else far + sl_buffer
                    signals.extend(_scan_matching_zones(store, eligibility, m5_flip_dir, "M5F", sl))

    # CISD triggers -- 2026-09-15, deliberately OUTSIDE the `if primary
    # is not None` block above (unlike all four triggers within it):
    # confirmed "no gate at all for CISD triggers", so these fire
    # regardless of M15 Primary Structure's current bias or even its
    # availability -- see reversal_entry.py's own module docstring for
    # the full confirmed design (identical here, just zones instead of
    # HTF levels). SL is the NEAREST ACTIVE SWING low/high
    # (cisd_bridge.sl_basis(), frozen at confirmation), NOT
    # last_cisd_level -- same same-day correction as reversal_entry.py's
    # own ("cuz they are very early"). sl_basis() returning None (no
    # active swing line existed at confirmation) means this trigger
    # simply produces no signal that cycle -- no fallback, no guess.
    m3_cisd = cisd_bridge.fresh_cisd(symbol, _M3_MINUTES)
    if m3_cisd is not None:
        basis = cisd_bridge.sl_basis(m3_cisd)
        if basis is not None:
            direction = cisd_bridge.direction_of(m3_cisd)
            sl = basis - sl_buffer if direction == 1 else basis + sl_buffer
            signals.extend(_scan_matching_zones(store, eligibility, direction, "M3CD", sl))

    m5_cisd = cisd_bridge.fresh_cisd(symbol, _M5_MINUTES)
    if m5_cisd is not None:
        basis = cisd_bridge.sl_basis(m5_cisd)
        if basis is not None:
            direction = cisd_bridge.direction_of(m5_cisd)
            sl = basis - sl_buffer if direction == 1 else basis + sl_buffer
            signals.extend(_scan_matching_zones(store, eligibility, direction, "M5CD", sl))

    return signals
