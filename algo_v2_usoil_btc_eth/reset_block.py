"""Manually release a timeframe's manual-intervention block for the merged
USOIL+BTCUSD+ETHUSD V2 bot.

Usage:
    python -m algo_v2_usoil_btc_eth.reset_block USOIL M5
    python -m algo_v2_usoil_btc_eth.reset_block BTCUSD M15
    python -m algo_v2_usoil_btc_eth.reset_block ETHUSD all
"""
from __future__ import annotations

import sys

from algo_v2_usoil_btc_eth.blocking import BlockedZoneStore
from algo_v2_usoil_btc_eth.config import load_config

VALID_TF = ("M5", "M15", "all")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[2] not in VALID_TF:
        print(f"Usage: python -m algo_v2_usoil_btc_eth.reset_block <symbol> <{'|'.join(VALID_TF)}>")
        raise SystemExit(1)

    requested_symbol, tf_arg = sys.argv[1], sys.argv[2]

    cfg = load_config()
    sym_cfg = next((s for s in cfg.symbols if s.symbol == requested_symbol), None)
    if sym_cfg is None:
        known = ", ".join(s.symbol for s in cfg.symbols)
        print(f"Unknown symbol {requested_symbol!r} -- known symbols: {known}")
        raise SystemExit(1)

    store = BlockedZoneStore(sym_cfg.blocked_state_file, sym_cfg.symbol)

    targets = ["M5", "M15"] if tf_arg == "all" else [tf_arg]
    for tf in targets:
        released = store.release(tf)
        if released is None:
            print(f"{sym_cfg.symbol} {tf}: no active block")
        else:
            print(f"{sym_cfg.symbol} {tf}: released block on {released}")


if __name__ == "__main__":
    main()
