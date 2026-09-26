"""Dynamic Zones Watcher -- Data Manager's 6th thread (2026-09-27).

SEPARATE from Major/Minor (user's own words: "this is seperate") --
DATA IMPORT ONLY, same as Major/Minor: no entry/exit logic reads this
yet, and no cross-check against Major/Minor is built here either -- both
explicitly deferred ("all logics to define later").

WHAT THIS IS: wraps rates.read_dynamic_zones() -- a direct port of
mql5/Dynamic_Zones_CISD_MajorMinor.mq5's own Dynamic Zones block (itself
a port of the "Dynamic Zone - Suraj v5" Pine script, confirmed against
the user's own pasted Pine source 2026-09-27, identical math). See that
function's own docstring and rates.py's own module-level Dynamic Zones
section for the exact Z1-Z4 rule.

SESSION BOUNDARY (2026-09-27, same day as the initial build -- matches
the MQL5 side's own fix): NOT MT5's native D1 bar (timestamped 00:00
UTC but that's just a label, confirmed live) and NOT a fixed UTC hour
either (the user's own live observation that the real gap shifts by an
hour with DST). A session boundary is any gap of more than one hour
between consecutive H1 bars -- see rates._dz_find_session_starts().

ALL 4 LINES (2026-09-27, same day, user: "these are the 4 lines needed
as per tradingview" after confirming live values matched closely) --
Z1/Z2 (resistance band) and Z3/Z4 (support band), not just the outer
Z2/Z4 pair.

CACHING ("dynamic zones plots when market opens and they stay, they are
again plotted next day" -- user, 2026-09-27): only persists a new state
file when the CURRENT session's own start time changes (a new session
has opened) -- rather than blindly rewriting the same numbers every
cycle. Heartbeat is still written every cycle regardless (same "written
unconditionally" convention every heartbeat in this project uses) -- a
stale heartbeat still means this thread died, even on a day where its
own state file legitimately never changes again after the first cycle.

Purely data-tracking, same category as every other Data Manager watcher
-- no MT5 orders are ever placed here. Read-only against the broker
(copy_rates only); only ever writes its own state/heartbeat files.

Run with: python -m v7_sentinel.dynamic_zones_watcher
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5
from dotenv import load_dotenv

from v7_sentinel import config, heartbeat, rates

load_dotenv()

_POLL_SECONDS = float(os.getenv("V7S_DYNAMIC_ZONES_POLL_SECONDS", "60"))


@dataclass(frozen=True)
class WatcherSymbolConfig:
    symbol: str
    state_file: str
    heartbeat_file: str
    poll_seconds: float


def load_symbol_configs() -> list[WatcherSymbolConfig]:
    return [
        WatcherSymbolConfig(
            symbol=symbol,
            state_file=config.state_file_for("dynamic_zones", symbol),
            heartbeat_file=config.state_file_for("dynamic_zones_watcher_heartbeat", symbol),
            poll_seconds=_POLL_SECONDS,
        )
        for symbol in config.ACTIVE_SYMBOLS
    ]


def _read_cached_session_start(state_file: str) -> Optional[int]:
    path = Path(state_file)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get("session_start_time")
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def run_once(cfg: WatcherSymbolConfig) -> None:
    snapshot = rates.read_dynamic_zones(cfg.symbol)
    if snapshot is None:
        print(f"[V7S-DYNZONE] {cfg.symbol} no data this cycle (not enough bar history yet)")
        return
    if _read_cached_session_start(cfg.state_file) == snapshot.session_start_time:
        return   # same session as last persisted -- "they stay," nothing to do
    print(f"[V7S-DYNZONE] {cfg.symbol} new session -- open={snapshot.open:.3f} "
          f"Z1={snapshot.z1:.3f} Z2={snapshot.z2:.3f} Z3={snapshot.z3:.3f} Z4={snapshot.z4:.3f}")
    Path(cfg.state_file).write_text(json.dumps(asdict(snapshot)))


def read_dynamic_zones_state(symbol: str) -> Optional[rates.DynamicZoneSnapshot]:
    """Cheap reader side -- no recompute, just deserializes whatever this
    watcher last persisted. None if the state file doesn't exist yet
    (watcher never completed a cycle for this symbol). Not called from
    anywhere yet -- data import only, see module docstring."""
    path = Path(config.state_file_for("dynamic_zones", symbol))
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return rates.DynamicZoneSnapshot(**raw)


def main() -> None:
    symbol_configs = load_symbol_configs()
    print(f"[V7S-DYNZONE] starting -- symbols={[c.symbol for c in symbol_configs]} poll={_POLL_SECONDS}s")

    if not mt5.initialize(path=config.MT5_TERMINAL_PATH):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        while True:
            for cfg in symbol_configs:
                try:
                    run_once(cfg)
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V7S-DYNZONE] {cfg.symbol} cycle error: {exc!r}")
                heartbeat.write(cfg.heartbeat_file)
            time.sleep(min(c.poll_seconds for c in symbol_configs))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
