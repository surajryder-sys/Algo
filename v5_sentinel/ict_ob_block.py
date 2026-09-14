"""TM-ICT's own OB zone data + live-tracking layer -- Trend Manager's
ICT component (see trend_manager_ict.py's own docstring for the full
entry-rule design). Scoped to ONE timeframe per instance (M3 for now,
M5 "possibly later" per the user's own words -- this module takes
tf_minutes as a parameter throughout rather than hardcoding M3, so
standing up an M5 instance later is just a second BlockStore/state file,
no new code).

Deliberately a SEPARATE store from nlb_nsb_block.py's own BlockStore, not
an extension of it -- that module is explicitly scoped to 6 HTF
timeframes (D1/H4/H1/M30/M15/M5) for TM/RM-STR's own ICT Guard proximity
safeguard, and the user's own words when building it were "M3 and M1 are
explicitly OUT of this Block's scope." Reusing it for M3 would risk
changing that safeguard's own behavior; this module exists purely for
TM-ICT's own needs instead.

TWO SOURCES, MERGED (confirmed 2026-09-14, user's Q4 answer): TradingView
(via the scraper's own raw zone store -- same file/schema ob_levels.py
reads, just an M3 key it doesn't scan) AND MT5 (ob_bridge_lite.py's
OBSTATE_LITE_{symbol}_{minutes}.json). Both sides seed into the SAME
store, deduped by (source, direction, start_time) -- "whichever is
recent and valid, start executions based on that" falls out naturally
from treating both sources' zones as equal members of one pool: when
comparing which zone actually fires this cycle, trend_manager_ict.py
sorts by formed_time descending, so the single most-recently-formed
zone across BOTH sources wins whenever more than one qualifies at once
-- exactly the user's own example ("if market is continuously falling,
and now suddenly we see a bullish ob on MT5, M3 timeframe, but tv bridge
doesn't get any, yes we can go based on MT5 confirmations").

SEEDING (sync(), one-time per zone): a zone already known here (by its
own stable id) is NEVER re-seeded or overwritten -- our own top/btm/
entry-plan governs its whole life, independent of whatever either
source's own copy does afterward (same "own tracking, don't keep
trusting the source" philosophy as nlb_nsb_block.py). At the moment a
zone is FIRST seeded, this module freezes:
  - entry_mode/entry_target ("MARKET"/"PENDING"/"NONE" -- Rules 1/2, see
    _entry_plan() below) off CURRENT live price at that exact moment,
    port of algo_v2/entries.py's own m3_or_m5_entry() with the pullback
    fraction changed 45%->40% per the user's explicit direction
    ("2nd rule entry at 40% from 4-12 points, earlier it was 45 now
    lets change to 40%"). Deliberately NOT using either source's own
    "detected_price" field for this distance measurement -- TV's own
    detected_price/detected_time read identical across every zone in the
    live store (looks like a last-scrape-update stamp, not a per-zone
    frozen snapshot) and MT5's own is 0 for any pre-attachment baseline
    zone (see ob_bridge_lite.py) -- neither is a reliable "price when
    this zone first became a candidate" value, so this module establishes
    its own, the same "seed once, own it from here" idiom used everywhere
    else in this project.
  - Nothing else needs freezing -- top/btm/formed_time never change once
    a zone exists, and the initial OB-based SL (ob edge +/- buffer) is a
    pure function of those, computed on demand by initial_sl() below
    rather than also stored.
  - A TV zone whose own "formed_time_confirmed" reads False is NEVER
    seeded at all (2026-09-14, found live: two real trades fired off
    zones with this flag False whose own price levels never
    corresponded to any real price action, confirmed against actual M1
    bars) -- tv_scraper itself sets this False when it couldn't read a
    real Pine formation-bar hint that poll and fell back to a wall-clock
    guess for start_time, which is exactly the class of scrape artifact
    behind both incidents. A genuinely real zone that started
    unconfirmed resurfaces here under its own corrected identity once
    tv_scraper's own 2-poll hint-correction rekeys it -- nothing is
    permanently lost by skipping it while unconfirmed.

LIVE INVALIDATION (update_live(), every tick/poll): same instant,
no-candle-close-wait mitigation rule as nlb_nsb_block.py -- a bullish
zone (bull, entry edge = top, approached from above) is invalidated the
moment bid trades below its own BOTTOM; a bearish zone (bear, entry edge
= bottom) is invalidated the moment ask trades above its own TOP. An
invalidated zone is deleted outright, not merely flagged.

This module does NOT do structure-based eligibility blocking (see
trend_manager_ict.py's own _zone_blocked()) -- that needs the live M3
BridgeBarFlipTracker state, which this module has no reason to depend
on; it stays pure OB-zone data + tracking, same separation of concerns
as nlb_nsb_block.py (data layer) vs. ict_guard.py (the consumer that
applies a structure-aware rule on top).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from v5_sentinel import ob_bridge_lite

PULLBACK_PCT = 0.40                  # Rule 2 -- 45% in algo_v2, changed to 40% for TM-ICT per explicit request
PULLBACK_MIN_EDGE_OFFSET = 4.0        # same floor as algo_v2/entries.py -- see that module's own docstring
MARKET_MAX_POINTS = 4.0                # Rule 1
PULLBACK_MIN_POINTS = 4.0
PULLBACK_MAX_POINTS = 12.0

_ROLE_BULL = "no_short_buffer"
_ROLE_BEAR = "no_long_buffer"


@dataclass
class ICTZone:
    symbol: str
    tf_minutes: int
    source: str                 # "tv" or "mt5"
    direction: str                # "bull" or "bear"
    role: str
    top: float
    btm: float
    formed_time: int              # the zone's own real formation bar (start_time)
    entry_mode: str                # "MARKET" | "PENDING" | "NONE" -- frozen at seed time, see module docstring
    entry_target: Optional[float]   # the PENDING pullback price; None for MARKET/NONE
    zone_id: str = ""

    @property
    def direction_int(self) -> int:
        return 1 if self.direction == "bull" else -1


def _zone_id(source: str, direction: str, start_time: int) -> str:
    return f"{source}|{direction}|{start_time}"


def _entry_plan(direction: int, edge: float, ref_price: float) -> tuple[str, Optional[float]]:
    """Port of algo_v2/entries.py's own m3_or_m5_entry(), pullback_pct
    changed to PULLBACK_PCT (0.40) per the user's explicit request.
    direction: 1 bullish (edge = zone top), -1 bearish (edge = zone btm).
    ref_price: the live price this zone is being measured against AT
    SEED TIME ONLY (see module docstring for why this is frozen, not the
    OB's own detected_price)."""
    distance = (ref_price - edge) if direction == 1 else (edge - ref_price)
    if distance < 0:
        return "NONE", None
    if distance <= MARKET_MAX_POINTS:
        return "MARKET", None
    if PULLBACK_MIN_POINTS < distance < PULLBACK_MAX_POINTS:
        offset = max(distance * (1 - PULLBACK_PCT), PULLBACK_MIN_EDGE_OFFSET)
        target = edge + offset if direction == 1 else edge - offset
        return "PENDING", target
    return "NONE", None


def initial_sl(zone: ICTZone, buffer: float) -> float:
    """Frozen OB-based SL -- "sl for ict is ob low with buffer" (bullish)
    / "ob high with buffer" (bearish), the FAR edge of the zone from
    whichever edge price approaches it from."""
    return zone.btm - buffer if zone.direction == "bull" else zone.top + buffer


class ICTBlockStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._zones: dict[str, ICTZone] = {}
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

    def sync(self, zone_state_file: str, symbol: str, tf_minutes: int, bid: float, ask: float) -> int:
        """Seeds every zone either source currently reports that this
        block has never seen before (by its own stable id). Returns how
        many new zones were seeded this call."""
        added = 0
        added += self._sync_tv(zone_state_file, symbol, tf_minutes, bid, ask)
        added += self._sync_mt5(symbol, tf_minutes, bid, ask)
        if added:
            self._save()
        return added

    def _seed(self, source: str, symbol: str, tf_minutes: int, direction: str, top: float, btm: float,
              start_time: int, bid: float, ask: float) -> bool:
        zid = _zone_id(source, direction, start_time)
        if zid in self._zones:
            return False
        direction_int = 1 if direction == "bull" else -1
        edge = top if direction == "bull" else btm
        ref_price = ask if direction_int == 1 else bid
        entry_mode, entry_target = _entry_plan(direction_int, edge, ref_price)
        role = _ROLE_BULL if direction == "bull" else _ROLE_BEAR
        self._zones[zid] = ICTZone(
            symbol=symbol, tf_minutes=tf_minutes, source=source, direction=direction, role=role,
            top=top, btm=btm, formed_time=start_time, entry_mode=entry_mode, entry_target=entry_target,
            zone_id=zid,
        )
        return True

    def _sync_tv(self, zone_state_file: str, symbol: str, tf_minutes: int, bid: float, ask: float) -> int:
        path = Path(zone_state_file)
        if not path.exists():
            return 0
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return 0
        added = 0
        for direction in ("bull", "bear"):
            key = f"{symbol}|{tf_minutes}|{direction}"
            for z in raw.get(key, {}).values():
                if not z.get("formed_time_confirmed", True):
                    # 2026-09-14, found live: two real trades fired off
                    # zones whose own price levels never corresponded to
                    # any real price action (confirmed against actual M1
                    # bars) -- both had this flag False, meaning
                    # tv_scraper itself couldn't read a real Pine
                    # formation-bar hint and fell back to a wall-clock
                    # guess for start_time (see ict_ob_block.py's own
                    # module docstring / tv_scraper/scraper.py's own
                    # formed_hint logic). Skip seeding entirely rather
                    # than trust it -- if this zone is genuinely real,
                    # tv_scraper's own 2-poll correction will eventually
                    # rekey it to a confirmed start_time, at which point
                    # it resurfaces here as a fresh, legitimately
                    # identified zone on its own next sync().
                    continue
                if self._seed("tv", symbol, tf_minutes, direction, float(z["top"]), float(z["btm"]),
                              int(z["start_time"]), bid, ask):
                    added += 1
        return added

    def _sync_mt5(self, symbol: str, tf_minutes: int, bid: float, ask: float) -> int:
        snap = ob_bridge_lite.read_lite(symbol, tf_minutes)
        if snap is None:
            return 0
        added = 0
        for direction, history in (("bull", snap.bull), ("bear", snap.bear)):
            for z in history:
                if self._seed("mt5", symbol, tf_minutes, direction, z.high, z.low, z.start_time, bid, ask):
                    added += 1
        return added

    def update_live(self, bid: float, ask: float) -> list[str]:
        """Instant, no-candle-close-wait invalidation -- see module
        docstring. Returns the zone_ids deleted this call."""
        invalidated: list[str] = []
        for zid, z in list(self._zones.items()):
            if z.direction == "bull":
                if bid < z.btm:
                    del self._zones[zid]
                    invalidated.append(zid)
            else:
                if ask > z.top:
                    del self._zones[zid]
                    invalidated.append(zid)
        if invalidated:
            self._save()
        return invalidated

    def zones(self) -> list[ICTZone]:
        return list(self._zones.values())
