"""OB-zone reversal-level reader for RM-ICT -- reads the tv_scraper's own
persisted zone store and narrows/classifies it down to exactly what this
component needs. Ported from v5_sentinel/ob_levels.py (2026-09-18) with
one fix: read_all_zones()/read_reversal_zones() hardcoded "XAUUSD" in
the zone-store lookup key even though every sibling module
(nlb_nsb_block.py) already took symbol as a parameter -- a real
single-symbol leftover this multi-instrument port needed to close. Both
functions now take `symbol` explicitly.

TIMEFRAME SCOPE REVISED 2026-09-18 (RM-ICT full redesign, see
reversal_ict.py's own docstring): H4, H2, H1, M30, M15, M5, M3 -- D1
dropped entirely (was never actually useful as a reversal-zone source
and its own grid-alignment offset couldn't be confidently validated
anyway), H2 and M3 newly added now that the scraper actually tracks
them and real zone data exists to confirm their own grid-alignment
offsets (see nlb_nsb_block.py's _TIMEFRAME_OFFSET).

M3 REMOVED 2026-09-22 (user: "remove m3 zones for reversals, lets keep
it from all HTF to M5") -- scope was H4, H2, H1, M30, M15, M5.

M1 REPLACED BY M10 2026-09-22 (user: "we have a major change in Scraper,
timeframes updated to H4, H2, H1, M30 top / M15, M10, M5, M3 bottom, no
more m1, we have nothing to do with m1 scraper data... now m10 becomes
eligible to store zone datas and even act on reversal trades"). The
scraper's own 8-pane grid no longer has an M1 chart at all -- that pane
now shows M10 instead (a TradingView-side layout change, no scraper CODE
change needed: scraper.py already reads each pane's timeframe live off
its own Data Window text, never a hardcoded per-pane assignment). M1 was
already excluded from this module's own TIMEFRAMES before this change
(so nothing here changes for M1 -- it simply stops being scraped at
all, which V7S was already indifferent to). M10 is NEW: it now gets
seeded/tracked/traded as a full reversal-zone candidate, same as every
other HTF here (see nlb_nsb_block.py's _TIMEFRAME_SECONDS/_OFFSET and
reversal_ict.py's _ZONE_CISD_POOLS for its own entries -- M10's own
grid-alignment offset is an ASSUMPTION (0, matching every other
<=H1 timeframe) until real M10 zone data can confirm it, same caveat
this module's own offset-derivation process always carries for a newly
added timeframe).

Current scope: H4, H2, H1, M30, M15, M10, M5. M3 stays excluded from
zones (unchanged from the removal above) but M3's own CISD DATA is
still very much a live confirmation source for other zones' pools --
unrelated to whether M3 OB zones themselves get seeded, same distinction
M1 always had.

Role naming: a bearish OB is a "no long buffer" -- a level price should
not be bought through/above (supply/resistance); a bullish OB is a
"no short buffer" -- a level price should not be sold through/below
(demand/support).

Reversal-zone classification: an UNTESTED (virgin) OB is a live reversal
candidate. A TESTED one (virgin=False, price has re-entered its range)
is no longer one. This module does no bookkeeping to "remove" a zone
once tested -- read_reversal_zones() always reads the scraper's store
fresh and filters on virgin==True, so a zone that gets tested simply
stops appearing on the very next read. Nothing to track, nothing to get
out of sync. Fully mitigated zones are already gone from the source
entirely -- ZoneStore.apply_mitigated() deletes them on confirmed
mitigation (see zone_store.py's own docstring).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Scraper's own raw timeframe keys (as read off each pane's Data Window
# header), highest to lowest.
TIMEFRAMES = ("240", "120", "60", "30", "15", "10", "5")

TIMEFRAME_NAMES = {
    "240": "H4", "120": "H2", "60": "H1", "30": "M30", "15": "M15", "10": "M10", "5": "M5",
}

_ROLE_BULL = "no_short_buffer"   # bullish OB -- demand/support, don't sell through it
_ROLE_BEAR = "no_long_buffer"    # bearish OB -- supply/resistance, don't buy through it


@dataclass(frozen=True)
class OBLevel:
    timeframe: str            # raw scraper key, e.g. "60"
    timeframe_name: str        # "H1"
    direction: str              # "bull" or "bear"
    role: str                    # "no_short_buffer" or "no_long_buffer"
    top: float
    btm: float
    formed_time: int             # start_time -- the zone's own formation bar
    formed_time_confirmed: bool   # False = wall-clock guess, not a real Pine hint
    virgin: bool
    retested_at: Optional[int]


def _role_for(direction: str) -> str:
    return _ROLE_BULL if direction == "bull" else _ROLE_BEAR


def _load_zone_store(zone_state_file: str) -> dict:
    path = Path(zone_state_file)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def read_all_zones(zone_state_file: str, symbol: str = "XAUUSD") -> list[OBLevel]:
    """Every OB (tested and untested) across the 6 configured timeframes
    -- full detail, for a caller that needs the whole picture, not just
    current reversal candidates. Newest-formed first within each
    timeframe/direction."""
    raw = _load_zone_store(zone_state_file)
    out: list[OBLevel] = []

    for tf in TIMEFRAMES:
        for direction in ("bull", "bear"):
            key = f"{symbol}|{tf}|{direction}"
            zones = raw.get(key, {})
            for z in sorted(zones.values(), key=lambda z: -z["start_time"]):
                out.append(OBLevel(
                    timeframe=tf,
                    timeframe_name=TIMEFRAME_NAMES[tf],
                    direction=direction,
                    role=_role_for(direction),
                    top=float(z["top"]),
                    btm=float(z["btm"]),
                    formed_time=int(z["start_time"]),
                    formed_time_confirmed=bool(z.get("formed_time_confirmed", True)),
                    virgin=bool(z.get("virgin", True)),
                    retested_at=int(z["retested_at"]) if z.get("retested_at") is not None else None,
                ))
    return out


def read_reversal_zones(zone_state_file: str, symbol: str = "XAUUSD") -> list[OBLevel]:
    """Only the CURRENT reversal-zone candidates -- untested (virgin)
    OBs across the 6 configured timeframes. Read fresh every call, so a
    zone that gets tested between calls simply stops appearing; no
    separate removal step exists or is needed."""
    return [lvl for lvl in read_all_zones(zone_state_file, symbol) if lvl.virgin]
