"""Maintains current OB zone state per (symbol, timeframe, direction), built
by folding tv_bridge ob_zone_formed/ob_zone_mitigated events, keyed by each
zone's start_time -- mirrors ob_bridge.OBSnapshot's Zone shape (high/low,
virgin, start_time, detected_time, detected_price) so future strategy logic
can treat TradingView-sourced zones the same way as MT5-sourced ones.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TVZone:
    start_time: int
    top: float
    btm: float
    avg: Optional[float]
    detected_time: int
    detected_price: float
    virgin: bool = True
    mitigated_time: Optional[int] = None
    mitigated_price: Optional[float] = None
    # Wall-clock time this zone was first observed RETESTED (price
    # re-entering [btm, top] after formation) -- distinct from
    # mitigated_time (LuxAlgo's full-invalidation/array-removal). See
    # tv_scraper/retest_tracker.py for why this is "first observed", not
    # the retest bar's true own time.
    retested_at: Optional[int] = None


class ZoneStore:
    """Keyed by "symbol|timeframe|direction" -> {start_time: TVZone}."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._zones: dict[str, dict[int, TVZone]] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str, direction: str) -> str:
        return f"{symbol}|{timeframe}|{direction}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for key, zones in raw.items():
            self._zones[key] = {int(st): TVZone(**z) for st, z in zones.items()}

    def _save(self) -> None:
        out = {
            key: {str(st): asdict(z) for st, z in zones.items()}
            for key, zones in self._zones.items()
        }
        self._path.write_text(json.dumps(out))

    def apply_formed(self, symbol: str, timeframe: str, direction: str, data: dict) -> None:
        key = self._key(symbol, timeframe, direction)
        zones = self._zones.setdefault(key, {})
        start_time = int(data["start_time"])
        # virgin here means "not yet RETESTED" -- matching the MT5 indicator's
        # own definition (OB_ATR_Bridge_Indicator_v1.00.mq5's virgin = !visited,
        # where visited comes from a dedicated retest check), not "not yet
        # mitigated"/removed-from-chart. Correct default for a zone that was
        # JUST formed: not yet retested. The alert path updates this later via
        # apply_retested() once OBD_SecretTrader.pine's own ob_zone_retested
        # webhook fires (carrying the exact retest bar time); tv_scraper sets
        # it directly here instead, from its own live-Close approximation.
        retested_at = data.get("retested_at")
        zones[start_time] = TVZone(
            start_time=start_time,
            top=float(data["top"]),
            btm=float(data["btm"]),
            avg=float(data["avg"]) if data.get("avg") is not None else None,
            detected_time=int(data["detected_time"]),
            detected_price=float(data["detected_price"]),
            virgin=bool(data.get("virgin", True)),
            retested_at=int(retested_at) if retested_at is not None else None,
        )
        self._save()

    def apply_mitigated(self, symbol: str, timeframe: str, direction: str, data: dict) -> None:
        key = self._key(symbol, timeframe, direction)
        zone = self._zones.get(key, {}).get(int(data["start_time"]))
        if zone is None:
            return  # mitigation for a zone we never saw formed -- ignore
        zone.virgin = False
        zone.mitigated_time = int(data["mitigated_time"])
        price = data.get("mitigated_price")
        zone.mitigated_price = float(price) if price is not None else None
        self._save()

    def apply_retested(self, symbol: str, timeframe: str, direction: str, data: dict) -> None:
        """From OBD_SecretTrader.pine's ob_zone_retested alert -- the EXACT
        bar time the retest happened, Pine's own knowledge, not tv_scraper's
        "whenever it happened to next poll" approximation. Never overwrites
        an earlier retested_at (e.g. tv_scraper's own approximation getting
        there first) with a later one -- whichever source noticed first
        stays authoritative for "when," even if this one is more precise."""
        key = self._key(symbol, timeframe, direction)
        zone = self._zones.get(key, {}).get(int(data["start_time"]))
        if zone is None:
            return  # retest for a zone we never saw formed -- ignore
        retested_time = int(data["retested_time"])
        zone.virgin = False
        if zone.retested_at is None or retested_time < zone.retested_at:
            zone.retested_at = retested_time
        self._save()

    def zones(self, symbol: str, timeframe: str, direction: str) -> list[TVZone]:
        """Newest first, matching ob_bridge.OBSnapshot's bull/bear ordering."""
        key = self._key(symbol, timeframe, direction)
        return sorted(self._zones.get(key, {}).values(), key=lambda z: -z.start_time)

    def get(self, symbol: str, timeframe: str, direction: str, start_time: int) -> Optional[TVZone]:
        """Direct lookup by exact start_time -- used by
        scraper._find_resurrectable() to check whether a candidate
        formation-time match (within its own tolerance window) corresponds
        to a real, already-known zone (including a currently-mitigated
        one, so a false-mitigation resurrection has something to resurrect
        FROM) before deciding to reuse its identity instead of minting a
        fresh one."""
        key = self._key(symbol, timeframe, direction)
        return self._zones.get(key, {}).get(start_time)

    def reload(self) -> None:
        """Re-reads the backing file -- see AtrStore.reload()'s docstring
        for the full rationale (same bug, same fix, same class of store)."""
        self._zones = {}
        self._load()
