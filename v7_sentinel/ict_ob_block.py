"""ICT Block -- the merged TV+MT5 OB-zone data layer for M15/M5/M3
(V7-Sentinel). Built 2026-09-24 for two features at once: RM-ICT's own
entries firing off MT5-sourced zones too (not just TV), and the new ICT
Exit component (exit_manager_ict.py), which needs M3/M5 zones from BOTH
sources for its fast-tier touch+CISD rule.

TWO SOURCES, CLASSIFIED (user's own words: "get them from MT5 bridge M15,
M5, M3 keep them in data manager classify TV and MT5 zones"): TradingView
(the scraper's own raw zone store, same file/schema ob_levels.py/
nlb_nsb_block.py read) AND MT5 (ob_bridge_lite.py's own
OBSTATE_LITE_{symbol}_{minutes}.json, published by the already-live
OB_Zone_Bridge_Lite.mq5). Every zone's own stable id is namespaced by
source (`f"{source}|{symbol}|{timeframe}|{direction}|{start_time}"`), so
the two sources coexist in one pool without ever colliding, even for the
literal same real price level reported by both at once.

DELIBERATELY A SEPARATE STORE from nlb_nsb_block.py's own BlockStore (its
scope is H4/H2/H1/M30/M15/M10/M5, TV-only, unchanged) -- NOT a
replacement or a narrowing of it. This means TV-sourced M15 and M5 zones
now exist in BOTH stores at once (nlb_nsb_block.py already covers those
two timeframes today; this store adds M3 on the TV side, plus all three
of M15/M5/M3 on the MT5 side). That overlap is harmless, not a
double-trade risk: RM-ICT's own entry lifecycle already treats "a
same-direction position already open" as a no-op regardless of which
zone/store triggered it (see reversal_ict.py's own docstring for why this
also gives cross-source dedup for free), and a duplicate EXIT check
against an already-closed position simply finds nothing to close. Keeping
this store narrowly scoped to M15/M5/M3 (rather than trying to unify it
with nlb_nsb_block.py's own H4-M10 scope) avoids touching that module's
existing, already-tuned behavior at all.

RETEST/INVALIDATION TRACKING is a straight port of nlb_nsb_block.py's own
design (retested/retested_at/retested_source fields, live tick-driven
update_live()) -- NOT V5-Sentinel's own ict_ob_block.py (that older
module was built for TM-ICT's distance-based immediate-entry rule
[entry_mode/entry_target], which has no "touch, then wait for a
confirming event" concept at all). Both RM-ICT's new MT5-zone entries and
the new ICT Exit component are touch+CISD driven, the exact same shape
nlb_nsb_block.py already solves -- reusing that design here (with a
`source` field added) is a better fit than porting the V5S module
verbatim.

SEEDING (sync(), one-time per zone, by its own stable id -- never
re-seeded or overwritten once known here):
  - TV side: read the SAME raw scraper store nlb_nsb_block.py reads, but
    for TIMEFRAMES ("15", "5", "3") instead of that module's own 7. A
    zone whose own "formed_time_confirmed" reads False is never seeded
    (same scrape-artifact guard nlb_nsb_block.py already uses). NOT
    pruned when the scraper stops reporting it -- nlb_nsb_block.py
    reversed that same policy 2026-09-21 ("a zone still on the chart just
    fell out of the scraper's own top-4-per-side view isn't
    invalidated") and this store follows that same, newer, more
    considered policy rather than V5S's own older per-source pruning.
  - MT5 side: read ob_bridge_lite.read_lite() for each of the 3
    timeframes. No formed_time_confirmed concept for this source (not a
    scrape, no wall-clock-guess fallback exists) -- always seeds.
  - Alignment/near-duplicate/cross-timeframe-duplicate guards (see
    _is_aligned_to_timeframe/_has_near_duplicate/
    _has_cross_timeframe_duplicate below) are ported from
    nlb_nsb_block.py verbatim, scoped to the TV side only -- these guard
    against real, confirmed tv_scraper misattribution bugs (a zone
    seeded under the wrong timeframe, or the same real zone re-detected
    under a new start_time); neither is a known failure mode of
    OB_Zone_Bridge_Lite's own, unrelated detection mechanism, and
    comparing across sources would risk false rejections (two different
    indicators coincidentally reporting the same real price level is
    expected, not a bug, since the whole point is to keep both as
    separate, classified entries).

LIVE RETEST/INVALIDATION (update_live(), every tick/poll, driven by
ict_ob_watcher.py) -- identical rule to nlb_nsb_block.py: a bullish zone
(role no_short_buffer, edge = top, approached from above) is retested the
moment price re-enters [btm, top] from above, invalidated (deleted
outright) the instant price trades below its own bottom; a bearish zone
(role no_long_buffer, edge = btm) is retested the moment price re-enters
from below, invalidated the instant price trades above its own top.

This store is READ-ONLY from every consumer's perspective (RM-ICT,
exit_manager_ict.py) -- only ict_ob_watcher.py (Data Manager's own
sub-component) ever calls sync()/update_live() on it.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from v7_sentinel import ob_bridge_lite, ob_levels

TIMEFRAMES = ("15", "5", "3")
TIMEFRAME_NAMES = {"15": "M15", "5": "M5", "3": "M3"}
_TIMEFRAME_MINUTES = {"15": 15, "5": 5, "3": 3}

_ROLE_BULL = "no_short_buffer"
_ROLE_BEAR = "no_long_buffer"

# Seconds-per-bar + each timeframe's own expected start_time remainder --
# same derivation nlb_nsb_block.py's own _TIMEFRAME_SECONDS/_OFFSET uses.
# M15/M5 values are the SAME already-derived ones that module uses today
# (remainder 0 for both, confirmed against real zone data there); M3's
# value is the same one that module's own docstring recorded before M3
# zones were removed from ITS scope (2026-09-22): "M3 remainder=0 for all
# 8 [zones then sampled], same as every other <=H1 timeframe here."
_TIMEFRAME_SECONDS = {"15": 900, "5": 300, "3": 180}
_TIMEFRAME_OFFSET = {"15": 0, "5": 0, "3": 0}
_ALIGNMENT_TOLERANCE_SECONDS = 1


def _is_aligned_to_timeframe(start_time: int, timeframe: str) -> bool:
    """See nlb_nsb_block.py's own identically-named function for the full
    "why" -- ported verbatim, TV side only (see module docstring)."""
    seconds = _TIMEFRAME_SECONDS.get(timeframe)
    if seconds is None:
        return True
    offset = _TIMEFRAME_OFFSET[timeframe]
    remainder = (start_time - offset) % seconds
    return remainder <= _ALIGNMENT_TOLERANCE_SECONDS or remainder >= seconds - _ALIGNMENT_TOLERANCE_SECONDS


def _role_for(direction: str) -> str:
    return _ROLE_BULL if direction == "bull" else _ROLE_BEAR


@dataclass
class ICTZone:
    symbol: str
    timeframe: str             # raw key, "15" | "5" | "3"
    timeframe_name: str          # "M15" | "M5" | "M3"
    source: str                    # "tv" or "mt5"
    direction: str                   # "bull" or "bear"
    role: str                          # "no_short_buffer" or "no_long_buffer"
    top: float
    btm: float
    formed_time: int                    # start_time -- the zone's own formation bar
    formed_time_confirmed: bool           # always True for "mt5" -- see module docstring
    retested: bool
    retested_at: Optional[int]             # OUR OWN timestamp -- wall-clock when WE marked it
    retested_source: str                     # "" (not yet), "seed" (already tested when first seen), "live" (we caught it ourselves)
    zone_id: str = ""


def _zone_id(source: str, symbol: str, timeframe: str, direction: str, start_time: int) -> str:
    return f"{source}|{symbol}|{timeframe}|{direction}|{start_time}"


class ICTBlockStore:
    """One instance per symbol (see config.state_file_for()). See module
    docstring for the full seeding/tracking design."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._zones: dict[str, ICTZone] = {}
        self._announced_rejections: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._zones = {}
            for zid, z in raw.items():
                zone = ICTZone(**z)
                if not zone.zone_id:
                    zone.zone_id = zid
                self._zones[zid] = zone
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._zones = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps({zid: asdict(z) for zid, z in self._zones.items()}))

    def _announce_rejection(self, key: str, message: str) -> None:
        if key not in self._announced_rejections:
            self._announced_rejections.add(key)
            print(message)

    # Same tight tolerance/rationale as nlb_nsb_block.py's own -- see that module's own comment.
    _PRICE_DUPLICATE_TOLERANCE = 0.05

    def _has_near_duplicate(self, symbol: str, timeframe: str, direction: str, top: float, btm: float) -> bool:
        """TV-side only -- see module docstring. Same real zone
        re-identified under a NEW start_time by the same source."""
        for z in self._zones.values():
            if z.source == "tv" and z.symbol == symbol and z.timeframe == timeframe and z.direction == direction \
                    and abs(z.top - top) <= self._PRICE_DUPLICATE_TOLERANCE \
                    and abs(z.btm - btm) <= self._PRICE_DUPLICATE_TOLERANCE:
                return True
        return False

    def _has_cross_timeframe_duplicate(self, symbol: str, timeframe: str, direction: str,
                                       start_time: int, top: float, btm: float) -> Optional[str]:
        """TV-side only -- see nlb_nsb_block.py's own identically-shaped
        function for the full "why" this catches a scraper
        misattribution (the same real zone seeded under the wrong
        timeframe)."""
        for z in self._zones.values():
            if z.source == "tv" and z.symbol == symbol and z.timeframe != timeframe and z.direction == direction \
                    and z.formed_time == start_time \
                    and abs(z.top - top) <= self._PRICE_DUPLICATE_TOLERANCE \
                    and abs(z.btm - btm) <= self._PRICE_DUPLICATE_TOLERANCE:
                return z.zone_id
        return None

    def sync(self, tv_zone_state_file: str, symbol: str) -> int:
        """Seeds every zone either source currently reports that this
        block has never seen before (by its own stable id). Returns the
        count added. See module docstring -- no pruning by absence for
        either source."""
        added = self._sync_tv(tv_zone_state_file, symbol) + self._sync_mt5(symbol)
        if added:
            self._save()
        return added

    def _sync_tv(self, zone_state_file: str, symbol: str) -> int:
        raw = ob_levels._load_zone_store(zone_state_file)
        added = 0
        for tf in TIMEFRAMES:
            for direction in ("bull", "bear"):
                key = f"{symbol}|{tf}|{direction}"
                for z in raw.get(key, {}).values():
                    if not z.get("formed_time_confirmed", True):
                        continue   # wall-clock guess, not a real Pine hint -- see module docstring
                    start_time = int(z["start_time"])
                    if not _is_aligned_to_timeframe(start_time, tf):
                        self._announce_rejection(
                            f"align|{symbol}|{tf}|{direction}|{start_time}",
                            f"[V7S-ICTBLOCK] zone tv|{symbol}|{tf}|{direction}|{start_time} REJECTED -- "
                            f"start_time isn't a valid {TIMEFRAME_NAMES[tf]} candle boundary")
                        continue
                    zid = _zone_id("tv", symbol, tf, direction, start_time)
                    if zid in self._zones:
                        continue
                    top, btm = float(z["top"]), float(z["btm"])
                    if self._has_near_duplicate(symbol, tf, direction, top, btm):
                        continue
                    dup_zid = self._has_cross_timeframe_duplicate(symbol, tf, direction, start_time, top, btm)
                    if dup_zid is not None:
                        self._announce_rejection(
                            f"xtf|{symbol}|{tf}|{direction}|{start_time}",
                            f"[V7S-ICTBLOCK] zone tv|{symbol}|{tf}|{direction}|{start_time} REJECTED -- "
                            f"exact match to existing cross-timeframe zone {dup_zid}")
                        continue
                    virgin = bool(z.get("virgin", True))
                    scraper_retested_at = z.get("retested_at")
                    self._zones[zid] = ICTZone(
                        symbol=symbol, timeframe=tf, timeframe_name=TIMEFRAME_NAMES[tf], source="tv",
                        direction=direction, role=_role_for(direction), top=top, btm=btm, formed_time=start_time,
                        formed_time_confirmed=True, retested=not virgin,
                        retested_at=int(scraper_retested_at) if (not virgin and scraper_retested_at is not None) else None,
                        retested_source="seed" if not virgin else "", zone_id=zid,
                    )
                    added += 1
        return added

    def _sync_mt5(self, symbol: str) -> int:
        added = 0
        for tf in TIMEFRAMES:
            snap = ob_bridge_lite.read_lite(symbol, _TIMEFRAME_MINUTES[tf])
            if snap is None:
                continue
            for direction, history in (("bull", snap.bull), ("bear", snap.bear)):
                for z in history:
                    zid = _zone_id("mt5", symbol, tf, direction, z.start_time)
                    if zid in self._zones:
                        continue
                    self._zones[zid] = ICTZone(
                        symbol=symbol, timeframe=tf, timeframe_name=TIMEFRAME_NAMES[tf], source="mt5",
                        direction=direction, role=_role_for(direction), top=z.high, btm=z.low,
                        formed_time=z.start_time, formed_time_confirmed=True, retested=not z.virgin,
                        retested_at=None, retested_source="seed" if not z.virgin else "", zone_id=zid,
                    )
                    added += 1
        return added

    def update_live(self, bid: float, ask: float, now: Optional[int] = None,
                    bid_low: Optional[float] = None, ask_high: Optional[float] = None) -> tuple[list[str], list[str]]:
        """Identical rule to nlb_nsb_block.BlockStore.update_live() -- see
        that method's own docstring. Returns (newly_retested_zone_ids,
        invalidated_zone_ids)."""
        now = now if now is not None else int(time.time())
        bid = bid if bid_low is None else min(bid, bid_low)
        ask = ask if ask_high is None else max(ask, ask_high)
        newly_retested: list[str] = []
        invalidated: list[str] = []
        changed = False

        for zid, z in list(self._zones.items()):
            if z.direction == "bull":
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

    def zones(self) -> list[ICTZone]:
        return list(self._zones.values())
