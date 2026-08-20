"""Maintains the latest ATR trail reading per (symbol, timeframe[, atr_period]),
built from tv_bridge atr_trail heartbeat events -- mirrors atr_bridge.ATRSnapshot.

atr_period support added 2026-08-20: OBD_ATR.pine can now run TWO
independent ATR periods on the same symbol+timeframe (e.g. a fast (2,2)
and slow (2,300) pair), each firing its own atr_trail event tagged with
which period it came from, specifically so "whichever period confirms
first" (the user's own stated reason for running two) can be evaluated --
without the atr_period key, the second period's event would silently
overwrite the first's in this store. A payload with NO atr_period field
(the single-ATR case, e.g. USOIL/USTEC's own single-line setup) still
keys exactly as before -- fully backward compatible, nothing needs to
change for a symbol that only ever runs one period.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TVAtrState:
    trail_stop: float
    trend: int  # 1 = strong (price above trail), -1 = weak (price below trail)
    received_at: float
    # Only populated by the alert-based path (tv_bridge); the scraper (pull)
    # path doesn't expose these via TradingView's Data Window, since
    # event_time's raw unix value would wreck the chart's price auto-scale
    # if plotted -- see pine/OBD_ATR.pine.
    event_time: Optional[int] = None
    bar_time: Optional[int] = None
    # Which ATR Period input this reading came from (OBD_ATR.pine's own
    # "atr_period" payload field, stringified) -- None for a payload that
    # never sent one (single-ATR setups, unchanged from before this field
    # existed). Stored on the object too (not just baked into the dict
    # key) so callers like AtrStore.get_all_for() can tell readings apart
    # without re-parsing the key string.
    atr_period: Optional[str] = None

    def age_seconds(self) -> float:
        return time.time() - self.received_at

    def is_stale(self, max_age_seconds: float = 60.0) -> bool:
        return self.age_seconds() > max_age_seconds


class AtrStore:
    """Keyed by "symbol|timeframe" (no atr_period given) or
    "symbol|timeframe|atr_period" (atr_period given) -> TVAtrState."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._state: dict[str, TVAtrState] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, timeframe: str, atr_period: Optional[str] = None) -> str:
        base = f"{symbol}|{timeframe}"
        return base if atr_period is None else f"{base}|{atr_period}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        self._state = {key: TVAtrState(**v) for key, v in raw.items()}

    def _save(self) -> None:
        self._path.write_text(json.dumps({k: asdict(v) for k, v in self._state.items()}))

    def apply(self, symbol: str, timeframe: str, data: dict, received_at: float) -> None:
        event_time = data.get("event_time")
        bar_time = data.get("bar_time")
        atr_period = data.get("atr_period")
        self._state[self._key(symbol, timeframe, atr_period)] = TVAtrState(
            trail_stop=float(data["trail_stop"]),
            trend=int(data["trend"]),
            received_at=received_at,
            event_time=int(event_time) if event_time is not None else None,
            bar_time=int(bar_time) if bar_time is not None else None,
            atr_period=str(atr_period) if atr_period is not None else None,
        )
        self._save()

    def get(self, symbol: str, timeframe: str, atr_period: Optional[str] = None) -> Optional[TVAtrState]:
        return self._state.get(self._key(symbol, timeframe, atr_period))

    def get_all_for(self, symbol: str, timeframe: str) -> list[TVAtrState]:
        """Every reading currently stored for this symbol+timeframe,
        across all ATR periods (plus the legacy no-period entry, if one
        exists) -- for a confirmation check that should fire on whichever
        period gets there first (see module docstring), not just one
        specific period. Order is whatever dict iteration gives, not
        meaningful -- callers needing a "first" reading should pick by
        event_time themselves, not by list order."""
        exact = self._key(symbol, timeframe)
        prefix = f"{exact}|"
        return [v for k, v in self._state.items() if k == exact or k.startswith(prefix)]

    def reload(self) -> None:
        """Re-reads the backing file, discarding whatever was in memory.
        For read-only consumers of a file THIS process doesn't write to
        (see algo_v2_tv_xauusd/reader.py) -- without this, a long-running
        reader that only ever calls __init__ once stays frozen at whatever
        the file contained at that exact moment forever, never seeing any
        later write from the actual writer process. Confirmed live: this
        made a continuously-running bot silently stop picking up new
        zones/ATR flips entirely after its first poll, with no error --
        each poll's `.get()`/`.zones()` just kept returning the same
        startup-time snapshot. Writers (tv_scraper, tradingview_bot.main)
        don't need this -- they own the file's mutations via apply(), so
        their own in-memory state is already authoritative."""
        self._load()
