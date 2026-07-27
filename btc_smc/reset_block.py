"""Manually release a timeframe's manual-intervention block -- the Python
equivalent of the BTCUSD indicator's on-chart RESET buttons.

Usage:
    python -m btc_smc.reset_block M5
    python -m btc_smc.reset_block M15
    python -m btc_smc.reset_block M30
    python -m btc_smc.reset_block all
"""
from __future__ import annotations

import sys

from btc_smc.blocking import BlockedZoneStore
from btc_smc.config import load_config

VALID = ("M5", "M15", "M30", "all")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in VALID:
        print(f"Usage: python -m btc_smc.reset_block <{'|'.join(VALID)}>")
        raise SystemExit(1)

    cfg = load_config()
    store = BlockedZoneStore(cfg.blocked_state_file, cfg.symbol)

    targets = ["M5", "M15", "M30"] if sys.argv[1] == "all" else [sys.argv[1]]
    for tf in targets:
        released = store.release(tf)
        if released is None:
            print(f"{tf}: no active block")
        else:
            print(f"{tf}: released block on {released}")


if __name__ == "__main__":
    main()
