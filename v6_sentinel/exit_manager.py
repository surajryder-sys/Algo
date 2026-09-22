"""V6-Sentinel Exit Manager -- a standalone process that watches every open position on the
account (TM-STR, RM-STR, RM-ICT) and closes trades opposite to a real signal, independent of
which manager opened them. Rebuilt from scratch 2026-09-21 (user: "lets re build the exit
manager from the beginning"), replacing the earlier entry-watching / timeframe-hierarchy
design entirely.

TWO COMPONENTS (user's own split, 2026-09-21):
  1. BIAS EXIT MANAGER (exit_manager_bias.py, built) -- a fresh M5 or M15 CISD closes every
     open position on the opposite side, across every manager, as long as that CISD's candle
     closed AFTER the position opened (an already-existing CISD never closes a trade). See
     exit_manager_bias.py's own docstring for the exact rule.
  2. LTF EXIT MANAGER -- not designed/built yet. A second, later addition; run_once() below
     already calls out where it plugs in once it exists.

Run with: python -m v6_sentinel.exit_manager

Safety: V6S_EM_{SYMBOL}_ENABLE_TRADING must be explicitly true for either component to send a
real close; left unset (default false) every decision is printed and logged but nothing
touches the account.
"""
from __future__ import annotations

import time

from v6_sentinel import broker, config, exit_manager_bias, heartbeat
from v6_sentinel.exit_manager_config import ExitManagerSymbolConfig, load_symbol_config


def run_once(cfg: ExitManagerSymbolConfig) -> None:
    exit_manager_bias.run_once(cfg)          # component 1: Bias Exit Manager
    # component 2 (LTF Exit Manager) goes here once it's designed


def main() -> None:
    cfgs = [load_symbol_config(symbol) for symbol in config.ACTIVE_SYMBOLS]
    for c in cfgs:
        print(f"[V6S-XM] {c.symbol} starting -- enable_trading={c.enable_trading} poll={c.poll_seconds}s "
              f"watching={[s.name for s in c.sources]} components=[bias]")
        broker.connect(c.symbol, c.mt5_terminal_path, c.mt5_login, c.mt5_password, c.mt5_server)
    try:
        while True:
            for c in cfgs:
                try:
                    run_once(c)
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive, log and continue
                    print(f"[V6S-XM] {c.symbol} cycle error: {exc!r}")
                heartbeat.write(c.heartbeat_file)
            time.sleep(min(c.poll_seconds for c in cfgs))
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
