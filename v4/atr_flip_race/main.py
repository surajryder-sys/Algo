"""ATR flip race -- observes which source (TradingView webhook push, or
tv_scraper pull) confirms a BTCUSD/ETHUSD M1 ATR-structure flip first, and
by how much, for real. Detection/logging ONLY -- places no orders, feeds
no execution engine (explicit scope, 2026-08-29; see race.py's own
docstring). Run with: python -m v4.atr_flip_race.main

Reads (never writes to) two already-running processes' own outputs:
  - v3/tv_bridge/receiver.py's shared signal log (webhook push path --
    must already be receiving real M1 atr_trail events for BTCUSD/ETHUSD;
    each symbol needs a TradingView Alert on its M1 chart, condition "Any
    alert() function call", pointed at the receiver's tunnel URL -- a
    one-time TradingView-side setup step, not something this process can
    do for you).
  - tv_scraper's own M1 combined-structure file for each symbol (pull
    path -- already running as of 2026-08-29, see
    [[project_tv_scraper_multi_symbol_setup]]).
"""
from __future__ import annotations

import datetime
import os
import time

from dotenv import load_dotenv

from v4.atr_flip_race.race import SYMBOLS, RaceState, poll_once

load_dotenv()

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _log(msg: str) -> None:
    ts = datetime.datetime.now(tz=_IST).strftime("%H:%M:%S")
    print(f"[atr_flip_race {ts} IST] {msg}")


def main() -> None:
    state_file = os.getenv("ATR_FLIP_RACE_STATE_FILE", "v4_atr_flip_race_state.json")
    signal_log = os.getenv("TV_SIGNAL_LOG_FILE", "tv_bridge_signals.jsonl")
    poll_seconds = float(os.getenv("ATR_FLIP_RACE_POLL_SECONDS", "2"))

    state = RaceState(state_file, signal_log)
    _log(f"watching BTCUSD/ETHUSD M1 -- webhook log={signal_log!r}, scraper files via scraper_read.py, "
         f"polling every {poll_seconds}s (detection only, no orders)")

    for sym in SYMBOLS:
        if state.webhook.current(sym)[1] is None:
            _log(f"NOTE: {sym} has no real M1 webhook data yet -- add a TradingView Alert on its M1 "
                 f"chart (condition: \"Any alert() function call\", pointed at the tunnel URL) to start "
                 f"racing it; until then {sym} will just report scraper's own timing with nothing to race")

    while True:
        for line in poll_once(state):
            _log(line)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
