"""Trend Manager -- the first Manager built inside Signal Engine (see
v3/signal_engine/__init__.py and the project_v3_crypto_architecture
memory note). Decides Structure and Short-term per symbol from the Data
Bridge's own OB zone data (v3/tradingview_bot/zone_store.py) -- M15 and
M5 respectively. No blended "bias" or "strong/weak" label -- by explicit
user decision, the two readings are reported plainly as-is; agreement or
disagreement between them is visible from the two values themselves,
not a separately computed field.

Rule (user's final, confirmed 2026-08-17):
- Structure = the direction (bullish/bearish) of whichever M15 OB
  (bull or bear) formed most recently for that symbol.
- Short term = the same, but for M5.
That's the whole rule. No recency comparison BETWEEN M5 and M15, no
Strong/Weak derived label -- M15 always decides Structure, M5 always
decides Short term, full stop.

Only counts zones with formed_time_confirmed=True (see
ZoneStore.TVZone's own docstring) -- a zone whose start_time is a
wall-clock guess rather than a real Pine-confirmed formation time can't
be trusted to actually BE the most recent OB; same reasoning Alert
Manager already applies before treating a zone as real.

Run with: python -m v3.signal_engine.trend_manager
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from v3.signal_engine.config import Config, load_config
from v3.tradingview_bot.zone_store import ZoneStore

_M15 = "15"
_M5 = "5"

_DIRECTION_LABELS = {"bull": "bullish", "bear": "bearish"}


@dataclass(frozen=True)
class TrendReading:
    symbol: str
    structure: Optional[str]    # "bullish" / "bearish" / None (no M15 data yet)
    short_term: Optional[str]   # "bullish" / "bearish" / None (no M5 data yet)


def _most_recent_direction(store: ZoneStore, symbol: str, timeframe: str) -> Optional[str]:
    """Most recent OB (bull or bear, whichever is younger) for this
    symbol/timeframe, by real Pine-confirmed start_time. None if there's
    no formed_time_confirmed zone on this timeframe at all yet."""
    best_start_time: Optional[int] = None
    best_direction: Optional[str] = None
    for direction in ("bull", "bear"):
        zones = store.zones(symbol, timeframe, direction)  # newest first
        for zone in zones:
            if not zone.formed_time_confirmed:
                continue
            if best_start_time is None or zone.start_time > best_start_time:
                best_start_time = zone.start_time
                best_direction = direction
            break  # zones() is newest-first -- first confirmed one is enough per direction
    if best_direction is None:
        return None
    return _DIRECTION_LABELS[best_direction]


def compute(store: ZoneStore, symbol: str) -> TrendReading:
    return TrendReading(
        symbol=symbol,
        structure=_most_recent_direction(store, symbol, _M15),
        short_term=_most_recent_direction(store, symbol, _M5),
    )


def _format_reading(reading: TrendReading) -> str:
    structure = reading.structure or "none"
    short_term = reading.short_term or "none"
    return f"{reading.symbol}: Structure {structure}, Short term {short_term}"


def run_once(cfg: Config) -> list[TrendReading]:
    readings = []
    for sym_cfg in cfg.symbols:
        store = ZoneStore(sym_cfg.zone_state_file)
        reading = compute(store, sym_cfg.symbol)
        readings.append(reading)
        print(f"[trend_manager] {_format_reading(reading)}")
    return readings


def main() -> None:
    cfg = load_config()
    print(f"[trend_manager] watching {[s.symbol for s in cfg.symbols]}, polling every {cfg.poll_seconds}s")
    while True:
        try:
            run_once(cfg)
        except Exception as exc:
            print(f"[trend_manager] ERROR: {exc}")
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
