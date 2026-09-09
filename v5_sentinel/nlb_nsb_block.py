"""NLB/NSB Block -- the algo-wide OB-zone data layer. Confirmed with the
user 2026-09-09: this is NOT scoped to the later RM-ICT sub-component
alone -- "these NSB and NLB we need to have it in the algo, not just for
ICT component, we gonna keep this for Trend Manager Structure based as
well." ob_levels.py's own read_all_zones()/read_reversal_zones() stay as
they are (a stateless, always-fresh READ of the scraper's own store) --
this module is a separate, STATEFUL layer that seeds from the scraper
once per zone and then tracks that zone's own retest/mitigation
INDEPENDENTLY from then on, for the specific reason below.

WHY a separate live tracker, not just reading the scraper's own
virgin/retested_at fields directly (user's own words, 2026-09-09):
"a retested status is not based on live tick in scraper, it is based on
candle close hopefully... we gonna only take zones from tv bridge and
keep them in our block... but once we have untested zones in our block,
lets watch for live retest through our internal program, lets not watch
or refer tv bridge... once we mark it is tested, we dont care what tv
bridge says about retest status." Same reasoning for mitigation, even
more pronounced: TradingView's own OB indicator only removes a zone once
a CANDLE CLOSES beyond it (or, per the user's own chart observation,
sometimes lags a full bar behind even a wick breach) -- "mitigation also
we not gonna wait till tv bridge confirmations... if it breaches beyond
zones, its mitigated... so we will delete it live from the block, we
will not wait for candle close." This module is the live, tick-driven,
authoritative alternative to both of those scraper-side signals.

SEEDING (one-time, per zone, the moment it's first seen -- sync_from_scraper()):
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
    here, completely independent of the scraper's own copy (which may
    still show it on-chart, may mark it retested later on a bar close,
    or may remove it on its own delayed mitigation logic -- none of that
    is read again after seeding).

LIVE RETEST (update_live(), every tick/poll):
  A zone becomes retested the moment live price re-enters its own
  [btm, top] range from the correct side -- bid for a bullish OB (NSB,
  price approaches from ABOVE, tests the TOP edge first), ask for a
  bearish OB (NLB, price approaches from BELOW, tests the BOTTOM edge
  first) -- same bid/ask convention reversal_entry.scan_touches() already
  uses for HTF levels. Checked and marked exactly once; a zone already
  retested is skipped (nothing left to detect).

LIVE MITIGATION/INVALIDATION (update_live(), every tick/poll): a zone is
invalidated the INSTANT price trades beyond its FAR edge -- below the
BOTTOM for a bullish OB (NSB), above the TOP for a bearish OB (NLB) --
no candle-close wait, no sustain requirement, a single tick is enough,
exactly matching the user's own worked example (2026-09-09):

  Bullish OB, top=4401.5, btm=4398.5 (the zone spans 4398.5-4401.5, its
  own "edge" -- the side price approaches from -- is always the TOP; a
  bearish OB is the mirror image, edge = BOTTOM):
    - Price 4401.5 -> 4398.3 -> back to 4402: INVALIDATED. Traded below
      4398.5 at some point, full stop -- doesn't matter that price came
      back, doesn't matter this never closed a candle there.
    - Price 4401.5 -> 4399 (never below 4398.5) -> back up, candle
      closes at 4400: RETESTED but NOT invalidated. The gap between
      4399 and 4398.5 was never actually traded, so the zone's own
      bottom edge was never breached -- still a live, valid NSB level,
      just no longer virgin.
  An invalidated zone is DELETED from the block outright (not merely
  flagged) -- matches the user's own "delete from block", and matches
  how a genuinely fully-mitigated OB has nothing further to track.

SCOPE: same 6 timeframes as ob_levels.py (D1, H4, H1, M30, M15, M5) --
reuses its TIMEFRAMES/TIMEFRAME_NAMES/_role_for() directly rather than
duplicating them, and reads the scraper's own raw zone store the same
way ob_levels.read_all_zones() does (same "symbol|timeframe|direction"
keying tv_scraper/zone_store.py already uses, so a zone's identity here
-- "symbol|timeframe|direction|start_time" -- lines up with the
scraper's own, letting sync_from_scraper() recognize "already seeded"
zones by that same key instead of guessing from top/btm values).

This module is intentionally just the DATA layer (store + seed + live
update) -- see nlb_nsb_watcher.py for the standalone process that
actually drives update_live() off real MT5 ticks every cycle, and
whatever consumes this block (TM's new NLB/NSB Block safeguard, RM-ICT)
reads it independently.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from v5_sentinel import ob_levels


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
    deletes it outright. See module docstring for the full design."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._zones: dict[str, BlockZone] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._zones = {zid: BlockZone(**z) for zid, z in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._zones = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({zid: asdict(z) for zid, z in self._zones.items()}))

    def sync_from_scraper(self, zone_state_file: str, symbol: str = "XAUUSD") -> int:
        """Seeds every zone the scraper currently reports that this block
        has never seen before (by its own stable id) -- never touches a
        zone already present here. Returns how many new zones were
        seeded this call (0 most cycles -- new OBs don't form often)."""
        raw = ob_levels._load_zone_store(zone_state_file)
        added = 0
        for tf in ob_levels.TIMEFRAMES:
            for direction in ("bull", "bear"):
                key = f"{symbol}|{tf}|{direction}"
                for z in raw.get(key, {}).values():
                    start_time = int(z["start_time"])
                    zid = _zone_id(symbol, tf, direction, start_time)
                    if zid in self._zones:
                        continue  # already ours -- our own state governs from here, not the scraper's
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
                    )
                    added += 1
        if added:
            self._save()
        return added

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
