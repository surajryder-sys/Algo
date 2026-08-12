"""Manually release a timeframe's manual-intervention block for the TVX bot.
The only reset path this bot has -- unlike algo_v2, there's no MT5 chart
tied to this data source to put RESET buttons on (see blocking.py).

Usage:
    python -m algo_v2_tv_xauusd.reset_block M1
    python -m algo_v2_tv_xauusd.reset_block M3
    python -m algo_v2_tv_xauusd.reset_block M5
    python -m algo_v2_tv_xauusd.reset_block all
"""
from __future__ import annotations

import sys

from algo_v2_tv_xauusd.blocking import BlockedZoneStore
from algo_v2_tv_xauusd.config import load_config

VALID = ("M1", "M3", "M5", "all")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in VALID:
        print(f"Usage: python -m algo_v2_tv_xauusd.reset_block <{'|'.join(VALID)}>")
        raise SystemExit(1)

    cfg = load_config()
    store = BlockedZoneStore(cfg.blocked_state_file)

    targets = ["M1", "M3", "M5"] if sys.argv[1] == "all" else [sys.argv[1]]
    for tf in targets:
        released = store.release(tf)
        if released is None:
            print(f"{tf}: no active block")
        else:
            print(f"{tf}: released block on {released}")


if __name__ == "__main__":
    main()
