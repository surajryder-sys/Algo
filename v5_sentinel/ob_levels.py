"""OB-zone reversal-level reader for the second Reversal Manager
component -- reads the tv_scraper's own persisted zone store
(v5s_tv_scraper_zones.json) and narrows/classifies it down to exactly
what this component needs. Confirmed with the user 2026-09-09.

Scope: only 6 of the scraper's 8 tracked timeframes -- D1, H4, H1, M30,
M15, M5. M3 and M1 keep getting scraped/stored (the chart still shows
them, and dropping panes would break the grid), they're just not read
by anything here.

Role naming (user's own terms, 2026-09-09): a bearish OB is a
"no long buffer" -- a level price should not be bought through/above
(supply/resistance); a bullish OB is a "no short buffer" -- a level
price should not be sold through/below (demand/support).

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
# header) -- D1 is the string "1D" like TradingView's own day label,
# everything else is plain minutes. See tv_scraper/parser.py's own
# _normalize_timeframe() for why D1 alone doesn't collapse to a number.
TIMEFRAMES = ("1D", "240", "60", "30", "15", "5")

TIMEFRAME_NAMES = {
    "1D": "D1", "240": "H4", "60": "H1", "30": "M30", "15": "M15", "5": "M5",
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


def read_all_zones(zone_state_file: str) -> list[OBLevel]:
    """Every OB (tested and untested) across the 6 configured timeframes
    -- full detail, for a caller that needs the whole picture, not just
    current reversal candidates. Newest-formed first within each
    timeframe/direction."""
    raw = _load_zone_store(zone_state_file)
    out: list[OBLevel] = []

    for tf in TIMEFRAMES:
        for direction in ("bull", "bear"):
            key = f"XAUUSD|{tf}|{direction}"
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


def read_reversal_zones(zone_state_file: str) -> list[OBLevel]:
    """Only the CURRENT reversal-zone candidates -- untested (virgin)
    OBs across the 6 configured timeframes. Read fresh every call, so a
    zone that gets tested between calls simply stops appearing; no
    separate removal step exists or is needed."""
    return [lvl for lvl in read_all_zones(zone_state_file) if lvl.virgin]
