"""NLB/NSB Block -- the algo-wide OB-zone data layer. Ported from
v5_sentinel/nlb_nsb_block.py (2026-09-18) -- already fully
symbol-parametrized throughout (zone_id includes symbol, duplicate/
pruning checks are explicitly symbol-scoped), so no multi-instrument
change needed beyond the one already-fixed leftover in ob_levels.py.
NOT scoped to RM-ICT alone -- this is meant to serve every component
that needs OB-zone data (TM-STR's/RM-STR's own ICT Guard safeguard,
RM-ICT's own zone eligibility).

Reads V5-Sentinel's OWN tv_scraper output file, read-only (confirmed
with the user 2026-09-18 -- see project_v6_sentinel_architecture memory
for the full reasoning: avoids a second scraper/browser window and the
already-documented maximized-window/CDP-conflict risk; V6S accepts a
runtime dependency on V5S's tv_scraper process staying alive as the
trade-off). NEVER write to that file from this module or its caller.

ob_levels.py's own read_all_zones()/read_reversal_zones() stay as a
stateless, always-fresh READ of the scraper's own store -- this module
is a separate, STATEFUL layer that seeds from the scraper once per zone
and then tracks that zone's own retest/mitigation INDEPENDENTLY from
then on.

WHY a separate live tracker, not just reading the scraper's own
virgin/retested_at fields directly: the scraper's own retested status
is based on candle close, not live ticks -- once a zone is in this
block, retest/mitigation are tracked purely off live MT5 ticks from
then on, ignoring whatever the scraper's own fields do afterward.

SEEDING (one-time, per zone, the moment it's first seen --
sync_from_scraper()):
  - A zone the scraper reports as virgin=True (untested) is seeded
    retested=False -- this module now owns tracking its retest live,
    from this point on, ignoring whatever the scraper's own
    virgin/retested_at fields do afterward.
  - A zone the scraper ALREADY reports virgin=False (tested before we
    ever saw it -- historic) is seeded retested=True immediately, using
    the scraper's own retested_at as a best-effort timestamp (source
    "seed", not "live" -- we didn't personally observe this one).
  - A zone already present in this block (by its own stable id) is NEVER
    re-seeded or overwritten from the scraper again -- once a zone is
    ours, our own top/btm/retested state governs for its entire life
    here, independent of whatever the scraper's own copy's FIELDS do
    afterward. ITS CONTINUED EXISTENCE is a different matter, though --
    see PRUNING below: this store's own copy is deleted once the scraper
    stops reporting the zone AT ALL, even though its own fields stay
    un-re-read the whole time it does exist.

PRUNING (also sync_from_scraper()): every zone this block is holding
that the scraper no longer reports at all gets removed, EVEN one
currently holding a sticky ICT Guard block (ict_guard.py's own
ICTGuardStickyStore.prune() already clears a standing block the moment
its zone leaves the Block, by design). Already-traded eligibility
(ICTEligibilityStore) is UNAFFECTED by pruning -- it stores its own
top/btm range independently at trade time, not a live reference into
this store. Live invalidation (price trading through a zone, via MT5's
own live bid/ask -- see update_live() below) stays completely separate
and unchanged by this.
  - A zone whose own "formed_time_confirmed" reads False is NEVER seeded
    at all -- a real V5S incident: a zone with this flag False had its
    top edge already above live price the instant it was seeded,
    auto-qualifying as "retested" with no genuine price action. A
    genuinely real zone that started unconfirmed resurfaces here under
    its own corrected identity once tv_scraper's own 2-poll hint-
    correction rekeys it.

LIVE RETEST (update_live(), every tick/poll): a zone becomes retested
the moment live price re-enters its own [btm, top] range from the
correct side -- bid for a bullish OB (NSB, price approaches from ABOVE,
tests the TOP edge first), ask for a bearish OB (NLB, price approaches
from BELOW, tests the BOTTOM edge first). Checked and marked exactly
once; a zone already retested is skipped.

LIVE MITIGATION/INVALIDATION (update_live(), every tick/poll): a zone is
invalidated the INSTANT price trades beyond its FAR edge -- below the
BOTTOM for a bullish OB (NSB), above the TOP for a bearish OB (NLB) --
no candle-close wait, no sustain requirement, a single tick is enough.
An invalidated zone is DELETED from the block outright (not merely
flagged).

SCOPE: same 7 timeframes as ob_levels.py (H4, H2, H1, M30, M15, M5, M3)
-- reuses its TIMEFRAMES/TIMEFRAME_NAMES/_role_for() directly rather than
duplicating them, and reads the scraper's own raw zone store the same
way ob_levels.read_all_zones() does (same "symbol|timeframe|direction"
keying tv_scraper/zone_store.py already uses, so a zone's identity here
-- "symbol|timeframe|direction|start_time" -- lines up with the
scraper's own, letting sync_from_scraper() recognize "already seeded"
zones by that same key instead of guessing from top/btm values).

This module is intentionally just the DATA layer (store + seed + live
update) -- a standalone process (V6S's own nlb_nsb_watcher, not yet
built) actually drives update_live() off real MT5 ticks every cycle,
and whatever consumes this block (an ICT Guard safeguard, RM-ICT) reads
it independently.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from v6_sentinel import ob_levels

# Seconds-per-bar, and each timeframe's OWN expected remainder within
# that period -- see _is_aligned_to_timeframe()'s own docstring for why
# the remainder isn't just 0 for every timeframe.
#
# Derived (V5S, 2026-09-16) by tallying start_time % period_seconds
# across EVERY zone currently in the live Block, per timeframe, and
# taking the clear plurality value -- NOT from MT5's own
# copy_rates_from_pos (an EARLIER attempt to cross-check against that
# gave 0 for every timeframe including H4, which directly contradicted
# H4's own zone data showing a consistent 3600s/1hr offset across 8
# months of real zones -- MT5's broker feed and TradingView's own
# OB-indicator feed turned out to run on DIFFERENT vendor session grids
# for at least H4, so MT5's own candle grid is the wrong reference to
# validate TradingView-sourced zones against). H2 (2026-09-18, V6S's own
# RM-ICT redesign, once H2 zones actually existed to check) and M3 were
# derived the same way, from the real (small: 8 zones each) samples
# then available: H2 remainder=3600 for all 8 (same session-boundary
# quirk as H4 -- both are >H1 timeframes), M3 remainder=0 for all 8
# (same as every other <=H1 timeframe here). D1 dropped entirely from
# this module's own scope (see ob_levels.py) -- its own remainder
# distribution never showed a clear plurality, so it's no longer
# relevant to keep an entry for.
_TIMEFRAME_SECONDS = {"240": 14400, "120": 7200, "60": 3600, "30": 1800, "15": 900, "5": 300, "3": 180}
_TIMEFRAME_OFFSET = {"240": 3600, "120": 3600, "60": 0, "30": 0, "15": 0, "5": 0, "3": 0}
# A start_time exactly ONE SECOND past the expected offset is a known,
# benign, unexplained-but-consistent variant seen throughout V5S's own
# real zones (paired entries like ...1786082400/...1786082401) -- NOT
# corruption, tolerated rather than rejected.
_ALIGNMENT_TOLERANCE_SECONDS = 1


def _is_aligned_to_timeframe(start_time: int, timeframe: str) -> bool:
    """True iff start_time is a genuinely valid candle-open for THIS
    timeframe's own bar grid (within _ALIGNMENT_TOLERANCE_SECONDS).
    Added in V5S after a real RM-ICT SELL fired off an "H1" zone
    provably impossible as a genuine H1 open (1620 seconds past H1's
    own expected 0-offset) but a clean M3 boundary -- confirmed live as
    a scraper misattribution, not a real H1 zone. This is a STRONGER
    defense than _has_cross_timeframe_duplicate() below: that guard only
    catches a SECOND copy of an already-existing zone, so a false zone
    seeded under the WRONG timeframe with no other copy anywhere else in
    the Block sails straight through it. This check rejects a false zone
    on its own FIRST and only seeding attempt, no duplicate required.

    NOT applied to "1D" or M1/M3 (M1/M3 aren't in ob_levels.TIMEFRAMES
    at all, never seeded here) -- D1's own remainder distribution showed
    NO single clear plurality in V5S's own data (several comparably-
    sized clusters, spread over many months), unlike every other
    timeframe, which each had one dominant value. Not enough confidence
    to validate D1 without risking false rejections of genuine zones;
    left uninvestigated for now rather than guessed at.

    M30 is a genuine edge case, not a bug in this check: M30's own grid
    is exactly 2x M15's, so a mislabeled M15 zone lands on M30's own
    valid boundary (remainder 0) roughly half the time by pure
    coincidence -- unprovable via alignment alone (that half needs
    _has_cross_timeframe_duplicate() to catch it, if an M15 copy also
    exists to compare against). The OTHER half (remainder 900, exactly
    M30's own half-period) is unambiguous and rejected outright -- a
    genuine M30 bar can never open there."""
    seconds = _TIMEFRAME_SECONDS.get(timeframe)
    if seconds is None:
        return True  # "1D", or any timeframe we don't validate -- don't block on something we can't check
    offset = _TIMEFRAME_OFFSET[timeframe]
    remainder = (start_time - offset) % seconds
    return remainder <= _ALIGNMENT_TOLERANCE_SECONDS or remainder >= seconds - _ALIGNMENT_TOLERANCE_SECONDS


@dataclass
class BlockZone:
    symbol: str
    timeframe: str             # raw scraper key, e.g. "60"
    timeframe_name: str         # "H1"
    direction: str               # "bull" or "bear"
    role: str                     # "no_short_buffer" or "no_long_buffer"
    top: float
    btm: float
    formed_time: int              # start_time -- the zone's own formation bar
    formed_time_confirmed: bool
    retested: bool
    retested_at: Optional[int]     # OUR OWN timestamp -- wall-clock when WE marked it
    retested_source: str            # "" (not yet), "seed" (scraper already showed tested), "live" (we caught it ourselves)
    # Same value as this zone's own dict key in BlockStore -- carried
    # on the record itself too so a caller holding just a BlockZone
    # (from zones()/reversal_zones()) doesn't need the store's internal
    # dict to reference it later. Defaults "" so a block file written
    # before this field existed still loads -- BlockStore._load()
    # backfills it from the dict key the very next time that happens.
    zone_id: str = ""


def _zone_id(symbol: str, timeframe: str, direction: str, start_time: int) -> str:
    """Same identity scheme tv_scraper/zone_store.py already keys zones
    by (symbol|timeframe|direction), plus start_time -- a zone's own
    formation bar never changes, so this id is stable for that zone's
    entire life, in both the scraper's store and this one."""
    return f"{symbol}|{timeframe}|{direction}|{start_time}"


class BlockStore:
    """Persists the NLB/NSB Block -- every OB zone this module has ever
    seeded, plus its own independently-tracked retest state, until the
    moment (if ever) this module's own live tick data invalidates it and
    deletes it outright. See module docstring for the full design. One
    instance per symbol (see config.state_file_for()) -- every method
    below is already scoped to a single `symbol` argument, so a shared
    instance across symbols would need no internal change, but the
    project's confirmed convention is one file/instance per symbol
    regardless."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._zones: dict[str, BlockZone] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._zones = {}
            for zid, z in raw.items():
                zone = BlockZone(**z)
                if not zone.zone_id:  # backfill for a block file written before this field existed
                    zone.zone_id = zid
                self._zones[zid] = zone
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._zones = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({zid: asdict(z) for zid, z in self._zones.items()}))

    # Points tolerance for the same-price re-identification guard below --
    # see sync_from_scraper's own comment. Deliberately tight: every
    # duplicate found live in V5S matched to 2-3 decimal places exactly
    # (XAUUSD's own scale), so this only ever catches a genuine
    # re-identification of the same real zone, never two legitimately
    # distinct nearby zones (which sit points apart on this instrument,
    # not hundredths of a point).
    _PRICE_DUPLICATE_TOLERANCE = 0.05

    def _has_near_duplicate(self, symbol: str, timeframe: str, direction: str, top: float, btm: float) -> bool:
        for z in self._zones.values():
            if z.symbol == symbol and z.timeframe == timeframe and z.direction == direction \
                    and abs(z.top - top) <= self._PRICE_DUPLICATE_TOLERANCE \
                    and abs(z.btm - btm) <= self._PRICE_DUPLICATE_TOLERANCE:
                return True
        return False

    def _has_cross_timeframe_duplicate(self, symbol: str, timeframe: str, direction: str,
                                       start_time: int, top: float, btm: float) -> Optional[str]:
        """Returns the zone_id of an EXISTING zone under a DIFFERENT
        timeframe that shares this exact (direction, start_time, top,
        btm), or None. Found live in V5S: the same real M3 zone got
        seeded under H4, H1, AND M15 too over a ~2 hour window --
        proven impossible as genuine native zones on those timeframes
        (confirmed misaligned to every other timeframe's own grid --
        see _is_aligned_to_timeframe(), which now catches this class of
        bug directly and is checked FIRST in sync_from_scraper(), before
        this). This cross-timeframe check stays as a SECOND layer for
        the case _is_aligned_to_timeframe() can't cover on its own: two
        DIFFERENT timeframes whose own bar grids happen to share a
        common multiple (e.g. an M15 zone's start_time is also,
        incidentally, a valid M3 boundary -- M15 is 5x M3 -- so
        alignment alone wouldn't catch a real M15 zone mislabeled as
        M3). Root cause not fully nailed down in V5S -- most likely
        tv_scraper's own Data Window re-render lagging a pane-focus
        switch past its own settling window. This is a defensive
        backstop independent of that root cause: a start_time is only
        ever a valid native candle-open for ONE timeframe's own bar grid
        at its OWN finest resolution, so an EXACT match across two
        different timeframes can only mean a misattribution, never two
        genuinely distinct zones."""
        for z in self._zones.values():
            if z.symbol == symbol and z.timeframe != timeframe and z.direction == direction \
                    and z.formed_time == start_time \
                    and abs(z.top - top) <= self._PRICE_DUPLICATE_TOLERANCE \
                    and abs(z.btm - btm) <= self._PRICE_DUPLICATE_TOLERANCE:
                return z.zone_id
        return None

    def sync_from_scraper(self, zone_state_file: str, symbol: str = "XAUUSD") -> tuple[int, int]:
        """Seeds every zone the scraper currently reports that this block
        has never seen before (by its own stable id), THEN prunes every
        zone this block is holding that the scraper no longer reports at
        all. Returns (added, pruned).

        PRUNING -- a genuine architecture change from V5S's original
        design, not a bug fix: "at any point of given time, scraper
        should give us the data of only 4 bullish ob's and 4 bearish
        ob's from the all timeframes... no other zones should be kept
        with neither scraper nor the python memory." Before this,
        seeding was one-way and permanent -- once a zone was copied in
        here, NOTHING ever removed it again except live invalidation
        (price trading through it). tv_scraper's own ZoneStore already
        deletes a zone once it's genuinely missing from the Data Window
        for 2 consecutive polls -- this block just never re-checked
        against that ongoing truth after its own initial copy, so it
        kept accumulating zones tv_scraper itself had already deleted
        (confirmed live in V5S: 232 zones held here against a handful
        ever actually visible on chart at once).

        Deliberately prunes EVEN a zone currently holding a sticky ICT
        Guard block -- ict_guard.py's own ICTGuardStickyStore.prune()
        already clears a standing block the moment its zone leaves the
        Block, by design, so this is that existing mechanism finally
        being exercised as intended rather than dead code.
        Already-traded eligibility (ICTEligibilityStore) is UNAFFECTED
        -- it stores its own top/btm range independently at trade time,
        not a live reference into this store, so pruning a zone here
        never weakens overlaps_traded()'s own protection against
        re-trading the same real zone if it later flickers back under a
        new start_time."""
        raw = ob_levels._load_zone_store(zone_state_file)
        added = 0
        live_zone_ids: set[str] = set()
        for tf in ob_levels.TIMEFRAMES:
            for direction in ("bull", "bear"):
                key = f"{symbol}|{tf}|{direction}"
                for z in raw.get(key, {}).values():
                    if not z.get("formed_time_confirmed", True):
                        # A real V5S incident: an RM-ICT trade fired off
                        # a zone whose top edge was already above live
                        # price the instant it was seeded -- auto-
                        # qualifying as "retested" with no genuine price
                        # action at all. Skip seeding entirely when
                        # tv_scraper itself couldn't confirm a real Pine
                        # formation-bar hint -- a genuinely real zone
                        # that started unconfirmed resurfaces here under
                        # its own corrected identity once tv_scraper's
                        # 2-poll correction rekeys it.
                        continue
                    start_time = int(z["start_time"])
                    if not _is_aligned_to_timeframe(start_time, tf):
                        # A zone whose own start_time isn't even a valid
                        # candle-open for the timeframe it's CLAIMED under
                        # -- provably a misattribution, not a real zone
                        # for this timeframe at all. REJECTED, never
                        # seeded, regardless of whether a duplicate
                        # exists anywhere else.
                        print(f"[V6S-BLOCK] zone {symbol}|{tf}|{direction}|{start_time} "
                              f"[{float(z['btm']):.3f}-{float(z['top']):.3f}] REJECTED -- "
                              f"start_time isn't a valid {ob_levels.TIMEFRAME_NAMES.get(tf, tf)} "
                              f"candle boundary, provably not a genuine zone for this timeframe")
                        continue
                    zid = _zone_id(symbol, tf, direction, start_time)
                    # Counts as "currently live" for pruning purposes
                    # regardless of what happens below -- a genuinely
                    # aligned zone the scraper is STILL reporting must
                    # never be pruned, even if it turns out to be a
                    # near/cross-timeframe duplicate of something else
                    # (that only stops it being SEEDED again, not
                    # ongoing existence for whichever entry already
                    # legitimately owns this exact zone_id).
                    live_zone_ids.add(zid)
                    if zid in self._zones:
                        continue  # already ours -- our own state governs from here, not the scraper's
                    top = float(z["top"])
                    btm = float(z["btm"])
                    if self._has_near_duplicate(symbol, tf, direction, top, btm):
                        # Same real zone re-identified under a NEW
                        # start_time -- confirmed live in V5S (the
                        # underlying chart/indicator can re-form the
                        # exact same box, a Pine recompute/redraw quirk
                        # or the OB detector re-picking the same
                        # historical bar, and hand tv_scraper a
                        # genuinely DIFFERENT, even
                        # formed_time_confirmed=True, start_time for
                        # it). Without this guard the same real zone
                        # re-seeds under a fresh zone_id every time it
                        # flickers, which every zone_id-keyed consumer
                        # then treats as brand new.
                        continue
                    dup_zid = self._has_cross_timeframe_duplicate(symbol, tf, direction, start_time, top, btm)
                    if dup_zid is not None:
                        # Same real zone misattributed to a DIFFERENT
                        # timeframe -- see _has_cross_timeframe_duplicate's
                        # own docstring.
                        print(f"[V6S-BLOCK] zone {symbol}|{tf}|{direction}|{start_time} "
                              f"[{btm:.3f}-{top:.3f}] REJECTED -- exact match to existing "
                              f"cross-timeframe zone {dup_zid}, almost certainly the same "
                              f"real zone misattributed to a different timeframe")
                        continue
                    virgin = bool(z.get("virgin", True))
                    scraper_retested_at = z.get("retested_at")
                    self._zones[zid] = BlockZone(
                        symbol=symbol, timeframe=tf, timeframe_name=ob_levels.TIMEFRAME_NAMES[tf],
                        direction=direction, role=ob_levels._role_for(direction),
                        top=float(z["top"]), btm=float(z["btm"]),
                        formed_time=start_time,
                        formed_time_confirmed=bool(z.get("formed_time_confirmed", True)),
                        retested=not virgin,
                        retested_at=int(scraper_retested_at) if (not virgin and scraper_retested_at is not None) else None,
                        retested_source="seed" if not virgin else "",
                        zone_id=zid,
                    )
                    added += 1

        # Scoped to THIS symbol only -- live_zone_ids only ever reflects
        # what was just read for `symbol`, so a zone belonging to some
        # OTHER symbol (this store now genuinely CAN hold more than one,
        # if a caller ever shares one instance across symbols against
        # this module's own one-instance-per-symbol convention) must
        # never be judged against it.
        stale = [zid for zid, z in self._zones.items() if z.symbol == symbol and zid not in live_zone_ids]
        for zid in stale:
            z = self._zones.pop(zid)
            print(f"[V6S-BLOCK] zone {zid} [{z.btm:.3f}-{z.top:.3f}] PRUNED -- "
                  f"scraper no longer reports it (not on chart anymore)")
        pruned = len(stale)

        if added or pruned:
            self._save()
        return added, pruned

    def update_live(self, bid: float, ask: float, now: Optional[int] = None) -> tuple[list[str], list[str]]:
        """One live-tick pass over every zone currently in the block.
        Invalidation is checked BEFORE retest for the same zone (if a
        single tick jumps clean through the whole range, there's nothing
        meaningful left to mark "retested" on a zone about to be
        deleted). Returns (newly_retested_zone_ids, invalidated_zone_ids)."""
        now = now if now is not None else int(time.time())
        newly_retested: list[str] = []
        invalidated: list[str] = []
        changed = False

        for zid, z in list(self._zones.items()):
            if z.direction == "bull":
                # NSB -- edge is the TOP (price approaches from above).
                # Invalidated the instant price trades below the BOTTOM.
                if bid < z.btm:
                    del self._zones[zid]
                    invalidated.append(zid)
                    changed = True
                    continue
                if not z.retested and bid <= z.top:
                    z.retested, z.retested_at, z.retested_source = True, now, "live"
                    newly_retested.append(zid)
                    changed = True
            else:
                # NLB -- edge is the BOTTOM (price approaches from below).
                # Invalidated the instant price trades above the TOP.
                if ask > z.top:
                    del self._zones[zid]
                    invalidated.append(zid)
                    changed = True
                    continue
                if not z.retested and ask >= z.btm:
                    z.retested, z.retested_at, z.retested_source = True, now, "live"
                    newly_retested.append(zid)
                    changed = True

        if changed:
            self._save()
        return newly_retested, invalidated

    def zones(self) -> list[BlockZone]:
        """Every zone currently in the block (already-invalidated ones
        are gone outright, never returned here)."""
        return list(self._zones.values())

    def reversal_zones(self) -> list[BlockZone]:
        """Zones this block has never seen live-retested (or seeded as
        already-tested) -- the live reversal-zone candidates."""
        return [z for z in self._zones.values() if not z.retested]
